"""
A tiny page cache: one query per page, shared by every column of it.

Deephaven's TableDataService asks the backend for each column of a page separately. The backend
loads *all* columns of a page in one query and serves the individual column requests from that
result. Positions themselves are made immutable by qdb_prefix (a settled prefix), so this cache
is about cost (one query instead of one per column), not correctness; it also means even a row
whose segment changed (a row later than the settle lag) is never mixed within one page load.

An entry is reused only while it is fresh and holds at least the `min_rows` the caller needs, so
a tail page that has grown is reloaded.
"""

import threading
import time
from collections import OrderedDict

DEFAULT_TTL_SECONDS = 5.0
DEFAULT_MAX_ENTRIES = 8


class PageCache:
    def __init__(
        self, ttl_seconds=DEFAULT_TTL_SECONDS, max_entries=DEFAULT_MAX_ENTRIES, clock=time.monotonic
    ):
        self._ttl = ttl_seconds
        self._max = max_entries
        self._clock = clock
        self._entries = OrderedDict()  # key -> (loaded_at, page)
        # Held while loading: concurrent requests for the other columns of the same page wait
        # and then hit the cache, so one page costs one query instead of one per column.
        self._lock = threading.Lock()
        self.loads = 0

    def get(self, key, min_rows, loader):
        """Return the cached page for `key`, or call `loader()` to (re)load it."""
        with self._lock:
            entry = self._entries.get(key)
            now = self._clock()
            if entry is not None:
                loaded_at, page = entry
                if now - loaded_at <= self._ttl and page.num_rows >= min_rows:
                    self._entries.move_to_end(key)
                    return page
            page = loader()
            self.loads += 1
            self._entries[key] = (self._clock(), page)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)
            return page

    def get_range(self, offset, min_rows, max_rows, loader):
        """Rows [offset, offset + n) with min_rows <= n <= max_rows, from any cached page that
        covers [offset, offset + min_rows), else from `loader()` (a page starting at offset).

        Only valid when positions never change (qdb_prefix): Deephaven fills each column's
        page from its own current length, so the columns of one page ask for different
        offsets; serving them from one loaded range keeps that to one query.
        """
        with self._lock:
            now = self._clock()
            for start, (loaded_at, page) in reversed(self._entries.items()):
                if now - loaded_at > self._ttl:
                    continue
                if start <= offset and offset + min_rows <= start + page.num_rows:
                    self._entries.move_to_end(start)
                    n = min(max_rows, start + page.num_rows - offset)
                    return page.slice(offset - start, n)
            page = loader()
            self.loads += 1
            self._entries[offset] = (self._clock(), page)
            self._entries.move_to_end(offset)
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)
            return page

    def clear(self):
        with self._lock:
            self._entries.clear()
