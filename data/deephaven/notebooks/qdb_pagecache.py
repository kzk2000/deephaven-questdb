"""
A tiny page cache that makes column fetches atomic.

Deephaven's TableDataService asks the backend for each column of a page separately. If every
request runs its own query, rows that land out of timestamp order between the queries shift the
`LIMIT lo,hi` window, and the columns of one logical row come from different rows (price from
one trade, exchange from another). Instead the backend loads *all* columns for a page in one
query and serves the individual column requests from that snapshot.

An entry is reused only while it is fresh and holds at least the `min_rows` the caller needs, so
a tail page that has grown is reloaded, and every column of one request set still sees the same
snapshot because they share the key.
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

    def clear(self):
        with self._lock:
            self._entries.clear()
