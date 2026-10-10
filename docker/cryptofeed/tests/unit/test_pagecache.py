"""
Atomic column paging: all columns of a page must come from one snapshot. No server needed.
"""

import sys
import threading
from pathlib import Path

import pyarrow as pa
import pytest

notebooks = Path(__file__).parent.parent.parent.parent.parent / "data" / "deephaven" / "notebooks"
sys.path.insert(0, str(notebooks))

from qdb_pagecache import PageCache  # noqa: E402
from qdb_qwp import QwpReader  # noqa: E402


def _page(n, tag=0):
    return pa.table({"price": [float(tag)] * n, "size": [float(tag)] * n})


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_one_load_serves_every_column_of_a_page():
    cache, calls = PageCache(), []

    def loader():
        calls.append(1)
        return _page(100)

    for _column in ("timestamp", "exchange", "price", "size", "side"):
        cache.get(("trades", 0, 64000), 100, loader)
    assert len(calls) == 1


def test_a_grown_tail_page_is_reloaded():
    cache = PageCache()
    assert cache.get(("t", 0, 64000), 10, lambda: _page(10, tag=1)).num_rows == 10
    page = cache.get(("t", 0, 64000), 25, lambda: _page(25, tag=2))
    assert page.num_rows == 25 and page.column("price")[0].as_py() == 2.0
    assert cache.loads == 2


def test_entries_expire():
    clock = Clock()
    cache = PageCache(ttl_seconds=5.0, clock=clock)
    cache.get(("t", 0, 10), 1, lambda: _page(5, tag=1))
    clock.now = 4.0
    assert cache.get(("t", 0, 10), 1, lambda: _page(5, tag=2)).column("price")[0].as_py() == 1.0
    clock.now = 9.5  # 5.5 s after the load
    assert cache.get(("t", 0, 10), 1, lambda: _page(5, tag=3)).column("price")[0].as_py() == 3.0


def test_least_recently_used_pages_are_evicted():
    cache = PageCache(max_entries=2)
    for offset in (0, 10, 20):
        cache.get(("t", offset, 10), 1, lambda: _page(3))
    assert cache.loads == 3
    cache.get(("t", 20, 10), 1, lambda: _page(3))  # still cached
    assert cache.loads == 3
    cache.get(("t", 0, 10), 1, lambda: _page(3))  # evicted -> reload
    assert cache.loads == 4


def test_concurrent_column_requests_share_a_single_load():
    cache, started = PageCache(), threading.Event()
    calls, results = [], []

    def loader():
        calls.append(1)
        started.wait(0.2)
        return _page(50)

    def column_request():
        results.append(cache.get(("t", 0, 64000), 50, loader))

    threads = [threading.Thread(target=column_request) for _ in range(8)]
    for t in threads:
        t.start()
    started.set()
    for t in threads:
        t.join()
    assert len(calls) == 1
    assert len(results) == 8 and all(r is results[0] for r in results)


def test_different_pages_are_independent():
    cache = PageCache()
    a = cache.get(("t", 0, 10), 1, lambda: _page(3, tag=1))
    b = cache.get(("t", 10, 10), 1, lambda: _page(3, tag=2))
    assert a.column("price")[0].as_py() == 1.0 and b.column("price")[0].as_py() == 2.0


def test_qwp_page_all_is_a_single_select_star_with_limit():
    queries = []
    reader = QwpReader()
    reader.arrow = lambda sql: queries.append(sql) or pa.table({"x": [1]})
    page = reader.page_all("trades", 64000, 64000, "timestamp")
    assert queries == ["SELECT * FROM trades ORDER BY timestamp LIMIT 64000,128000"]
    assert page.num_rows == 1


@pytest.mark.parametrize("offset,count", [(0, 10), (5, 1)])
def test_qwp_page_all_window_bounds(offset, count):
    queries = []
    reader = QwpReader()
    reader.arrow = lambda sql: queries.append(sql) or pa.table({"x": [1]})
    reader.page_all("t", offset, count, "ts")
    assert queries[0].endswith(f"LIMIT {offset},{offset + count}")
