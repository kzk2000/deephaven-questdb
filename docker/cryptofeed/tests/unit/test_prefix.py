"""
Settled-prefix paging for TableDataService: positions below the published size never move.
No server needed: a fake table stands in for QuestDB.
"""

import random
import sys
from pathlib import Path

import pytest

notebooks = Path(__file__).parent.parent.parent.parent.parent / "data" / "deephaven" / "notebooks"
sys.path.insert(0, str(notebooks))

from qdb_prefix import StablePrefix, order_by, take_indices  # noqa: E402

S = 1_000_000_000  # ticks per second (ns)


class FakeTable:
    """Rows (ts, key) visible in QuestDB, plus a wall clock."""

    def __init__(self, now=100 * S):
        self.rows = set()
        self.now = now

    def insert(self, ts, key=None):
        self.rows.add((ts, key if key is not None else f"k{len(self.rows)}"))

    def count(self, lo, hi):
        return sum(1 for ts, _ in self.rows if lo <= ts < hi)

    def max_ts(self):
        return max((ts for ts, _ in self.rows), default=None)

    def buckets(self, lo, hi):
        out = {}
        for ts, _ in self.rows:
            if lo <= ts < hi:
                b = ts - ts % S
                out[b] = out.get(b, 0) + 1
        return sorted(out.items())

    def prefix(self, start=0, settle=1.0, **kw):
        return StablePrefix(
            self.count,
            self.max_ts,
            self.buckets,
            start=start,
            settle_seconds=settle,
            now=lambda: self.now,
            log=lambda msg: None,
            **kw,
        )

    def page(self, prefix, lo, hi):
        """What the backend serves for positions [lo, hi): one ordered range fetch."""
        base, bounds, counts = prefix.locate(lo, hi)
        fetched = sorted(r for r in self.rows if bounds[0] <= r[0] < bounds[-1])
        idx, _, _ = take_indices([ts for ts, _ in fetched], bounds, counts)
        rows = [fetched[i] if i is not None else None for i in idx]
        return rows[lo - base : hi - base]


def test_watermark_trails_max_and_clock_and_never_decreases():
    t = FakeTable(now=100 * S)
    t.insert(50 * S)
    p = t.prefix(settle=2.0)
    p.initialize()
    assert p.watermark == 48 * S  # min(max_ts=50, now=100) - 2
    t.insert(500 * S)  # a timestamp from the future: capped by the clock
    p.poll()
    assert p.watermark == 98 * S
    t.now = 90 * S  # clock steps back: the watermark does not
    p.poll()
    assert p.watermark == 98 * S


def test_initial_history_is_indexed_per_bucket():
    t = FakeTable(now=20 * S)
    for ts in [S // 2, S + 1, S + 2, 3 * S, 3 * S + 5, 9 * S]:
        t.insert(ts)
    p = t.prefix(settle=1.0)
    assert p.initialize() == 5  # the watermark is max(ts) - settle = 8 s
    assert p.bounds == [0, S, 3 * S, 8 * S]
    assert p.cum == [0, 1, 3, 5]
    assert t.page(p, 0, 5) == sorted(t.rows)[:5]


def test_rows_arriving_out_of_order_within_settle_never_move_published_positions():
    rng = random.Random(7)
    t = FakeTable(now=0)
    p = t.prefix(settle=1.0, segment_seconds=0.2)
    p.initialize()
    seen = {}  # position -> row, as first served
    for step in range(400):
        t.now += S // 20
        for _ in range(rng.randint(0, 6)):
            # up to 0.9 s late relative to the clock, i.e. within settle
            t.insert(t.now - rng.randint(0, 9 * S // 10), key=f"r{step}-{rng.random()}")
        p.poll()
        if p.size:
            lo = rng.randrange(p.size)
            hi = min(p.size, lo + rng.randint(1, 50))
            for pos, row in enumerate(t.page(p, lo, hi), start=lo):
                assert seen.setdefault(pos, row) == row
    assert p.late_rows == 0 and p.missing_rows == 0
    assert len(seen) > 100
    # the prefix is exactly the rows below the watermark, in order
    assert t.page(p, 0, p.size) == sorted(r for r in t.rows if 0 <= r[0] < p.watermark)


def test_ties_are_ordered_by_tiebreak_not_arrival():
    t = FakeTable(now=10 * S)
    for key in ["c", "a", "b"]:
        t.insert(S, key=key)
    t.insert(5 * S)
    p = t.prefix(settle=1.0)
    p.initialize()
    assert [r[1] for r in t.page(p, 0, 3)] == ["a", "b", "c"]


def test_a_row_later_than_settle_is_counted_and_only_touches_its_segment():
    t = FakeTable(now=0)
    p = t.prefix(settle=1.0, segment_seconds=1.0)
    p.initialize()
    for sec in range(1, 11):
        t.now = sec * S
        for i in range(5):
            t.insert(sec * S - S + i * S // 5, key=f"s{sec}-{i}")
        p.poll()
    before = t.page(p, 0, p.size)
    size = p.size
    t.insert(2 * S + 1, key="late")  # far below the watermark
    assert p.poll() is None or p.size >= size
    assert p.late_rows == 1
    after = t.page(p, 0, size)
    changed = [i for i in range(size) if before[i] != after[i]]
    seg = next(k for k in range(len(p.bounds) - 1) if p.bounds[k] <= 2 * S + 1 < p.bounds[k + 1])
    assert changed and all(p.cum[seg] <= i < p.cum[seg + 1] for i in changed)
    # detected once, not on every poll
    p.poll()
    assert p.late_rows == 1


def test_rows_dropped_by_ttl_become_nulls_without_shifting_the_rest():
    t = FakeTable(now=0)
    p = t.prefix(settle=1.0)
    p.initialize()
    for sec in range(1, 8):
        t.now = sec * S
        t.insert(sec * S - S // 2, key=f"s{sec}")
        p.poll()
    before = t.page(p, 0, p.size)
    t.rows = {r for r in t.rows if r[0] >= 3 * S}  # partition drop
    p.poll()
    assert p.missing_rows == 3
    after = t.page(p, 0, len(before))
    assert after[:3] == [None, None, None]
    assert after[3:] == before[3:]


def test_size_only_grows_and_poll_reports_growth():
    t = FakeTable(now=0)
    p = t.prefix(settle=0.5)
    assert p.initialize() == 0
    assert p.poll() is None
    t.insert(S // 10)
    t.insert(S)
    t.now = S
    assert p.poll() == 1  # the row at 1 s is not settled yet
    assert p.poll() is None
    t.rows.clear()
    assert p.poll() is None and p.size == 1


def test_short_segments_merge_but_keep_positions():
    t = FakeTable(now=0)
    p = t.prefix(settle=0.0, segment_seconds=1.0)
    p.initialize()
    for i in range(1, 21):
        t.insert(i * S // 10 - 1)
        t.now = i * S // 10
        p.poll()
    assert p.size == 19  # the newest row is never below max(ts) - 0
    assert len(p.bounds) <= 4  # 2 s of polls in ~1 s segments
    assert t.page(p, 0, 19) == sorted(t.rows)[:19]


def test_history_start_excludes_older_rows():
    t = FakeTable(now=10 * S)
    t.insert(S)
    t.insert(5 * S)
    t.insert(9 * S)
    p = t.prefix(start=3 * S, settle=1.0)
    assert p.initialize() == 1


@pytest.mark.parametrize(
    "cum,lo,hi,expected",
    [
        ([0, 0, 5, 9], 0, 3, (0, 1, 1)),  # empty first segment is skipped
        ([0, 4, 4, 9], 3, 6, (0, 0, 2)),  # spans an empty middle segment
        ([0, 4, 9], 8, 9, (4, 1, 1)),
    ],
)
def test_locate(cum, lo, hi, expected):
    p = FakeTable().prefix()
    p.cum = cum
    p.bounds = list(range(0, len(cum) * 10, 10))
    base, bounds, counts = p.locate(lo, hi)
    first, i, j = expected
    assert base == first
    assert bounds == p.bounds[i : j + 2]
    assert counts == [cum[k + 1] - cum[k] for k in range(i, j + 1)]


def test_take_indices_truncates_extra_and_pads_missing():
    ticks = [1, 2, 3, 11, 25]
    idx, extra, missing = take_indices(ticks, [0, 10, 20, 30], [2, 2, 1])
    assert idx == [0, 1, 3, None, 4]
    assert (extra, missing) == (1, 1)


def test_order_by_is_total():
    cols = ["timestamp", "exchange", "symbol", "price", "trade_id"]
    assert order_by(cols, ["timestamp", "exchange", "symbol", "trade_id"]) == [
        "timestamp",
        "exchange",
        "symbol",
        "trade_id",
        "price",
    ]
    assert order_by(["timestamp", "exchange", "bids"], [], array_columns=["bids"]) == [
        "timestamp",
        "exchange",
        "bids",
    ]
