"""
FeedCore: every row must be published exactly once, in spite of out-of-order arrival, with no
Deephaven or QuestDB needed.
"""

import sys
from pathlib import Path

import pyarrow as pa

notebooks = Path(__file__).parent.parent.parent.parent.parent / "data" / "deephaven" / "notebooks"
sys.path.insert(0, str(notebooks))

from qdb_feed import FeedCore, QueryCore, _iso_micros  # noqa: E402

NS = 1_000_000_000
T0 = 1_700_000_000 * NS


def rows(*spec):
    """spec items: (ts_seconds_offset, exchange, trade_id, price)"""
    return pa.table(
        {
            "timestamp": pa.array(
                [T0 + int(o * NS) for o, *_ in spec], pa.timestamp("ns", tz="UTC")
            ),
            "exchange": [e for _, e, _, _ in spec],
            "trade_id": [t for _, _, t, _ in spec],
            "price": [float(p) for *_, p in spec],
        }
    )


class FakeDb:
    """A QuestDB table that can be appended to; query() applies the timestamp lower bound."""

    def __init__(self):
        self.data = []  # list of (ts_ns, exchange, trade_id, price)
        self.queries = []

    def add(self, *spec):
        for o, e, t, p in spec:
            self.data.append((T0 + int(o * NS), e, t, float(p)))

    def query(self, sql):
        self.queries.append(sql)
        bound = sql.split("timestamp >= '")[1].split("'")[0]
        import datetime as dt

        floor = int(dt.datetime.fromisoformat(bound.replace("Z", "+00:00")).timestamp() * NS)
        sel = sorted((r for r in self.data if r[0] >= floor), key=lambda r: r[0])
        return pa.table(
            {
                "timestamp": pa.array([r[0] for r in sel], pa.timestamp("ns", tz="UTC")),
                "exchange": [r[1] for r in sel],
                "trade_id": [r[2] for r in sel],
                "price": [r[3] for r in sel],
            }
        )


def make_core(db, published, now_offset=100.0, **kw):
    return FeedCore(
        "trades",
        db.query,
        lambda t: published.append(t),
        key_columns=("timestamp", "exchange", "trade_id"),
        history_seconds=kw.pop("history_seconds", 3600),
        overlap_seconds=kw.pop("overlap_seconds", 3.0),
        clock=lambda: T0 / NS + now_offset,
        **kw,
    )


def published_ids(published):
    return [i for t in published for i in t.column("trade_id").to_pylist()]


def test_history_is_loaded_once_then_only_new_rows():
    db, out = FakeDb(), []
    db.add((1, "A", "t1", 100), (2, "A", "t2", 101))
    core = make_core(db, out)
    assert core.step() == 2
    assert core.step() == 0  # nothing new: the overlap re-reads t1/t2 but they are skipped
    db.add((3, "A", "t3", 102))
    assert core.step() == 1
    assert published_ids(out) == ["t1", "t2", "t3"]


def test_a_late_row_inside_the_overlap_is_appended_exactly_once():
    db, out = FakeDb(), []
    db.add((10, "A", "t10", 100), (11, "A", "t11", 101))
    core = make_core(db, out)
    core.step()
    # a row with an OLDER timestamp appears after newer ones (out-of-order arrival)
    db.add((10.5, "B", "late", 99))
    assert core.step() == 1
    assert core.step() == 0
    assert published_ids(out) == ["t10", "t11", "late"]


def test_a_row_later_than_the_overlap_is_not_seen():
    db, out = FakeDb(), []
    db.add((100, "A", "t100", 100))
    core = make_core(db, out, overlap_seconds=3.0)
    core.step()
    db.add((50, "B", "very-late", 90))  # 50 s older than the watermark
    assert core.step() == 0
    assert "very-late" not in published_ids(out)


def test_identical_key_is_one_row_even_if_the_value_changes():
    db, out = FakeDb(), []
    db.add((5, "A", "t5", 100))
    core = make_core(db, out)
    core.step()
    db.add((5, "A", "t5", 100.5))  # a replay/upsert of the same trade
    assert core.step() == 0
    assert published_ids(out) == ["t5"]


def test_same_timestamp_different_trade_ids_are_both_kept():
    db, out = FakeDb(), []
    db.add((7, "A", "x", 100), (7, "A", "y", 100), (7, "B", "x", 100))
    core = make_core(db, out)
    assert core.step() == 3
    assert sorted(published_ids(out)) == ["x", "x", "y"]


def test_poll_query_uses_the_watermark_minus_overlap_and_where():
    db, out = FakeDb(), []
    db.add((20, "A", "t", 1))
    core = make_core(db, out, overlap_seconds=2.0, where="contract_type <> 'spot'")
    core.step()
    core.step()
    first, second = db.queries
    assert "contract_type <> 'spot'" in first and first.startswith("SELECT * FROM trades WHERE")
    # watermark is T0+20 s; the poll starts 2 s earlier
    assert _iso_micros(T0 + 18 * NS) in second
    assert second.endswith("ORDER BY timestamp")


def test_empty_results_do_not_move_the_watermark():
    db, out = FakeDb(), []
    core = make_core(db, out)
    assert core.step() == 0
    assert core.watermark_ns is None
    db.add((1, "A", "t1", 1))
    assert core.step() == 1
    assert core.watermark_ns == T0 + 1 * NS


def test_seen_keys_are_pruned_so_memory_stays_bounded():
    db, out = FakeDb(), []
    core = make_core(db, out, overlap_seconds=1.0)
    n = 3000  # 100 rows per second over 30 s
    for i in range(n):
        db.add((i * 0.01, "A", f"t{i}", 1))
    core.step()
    assert core.rows_published == n
    # only keys within two overlaps (2 s = ~200 rows) of the watermark are worth remembering
    assert len(core._seen) < 400

    # a long run of steady traffic never lets the set grow past the pruning guard
    for step in range(1, 40):
        base = 100 * step
        db.add(*[(base + i * 0.01, "A", f"s{step}-{i}", 1) for i in range(300)])
        core.step()
    assert len(core._seen) <= 1000 + 600
    assert core.rows_published == n + 39 * 300


def test_iso_micros_format():
    assert _iso_micros(1_700_000_000 * NS + 123_456_789) == "2023-11-14T22:13:20.123456Z"


def test_nanosecond_timestamps_work_as_keys_without_pandas():
    db, out = FakeDb(), []
    db.add((1.000000123, "A", "t1", 1), (2.000000456, "A", "t2", 2))  # not microsecond aligned
    core = make_core(db, out)
    assert core.step() == 2
    assert core.step() == 0


def test_the_default_is_a_bounded_ring_table():
    import qdb_feed

    assert qdb_feed.DEFAULT_CAPACITY is not None and qdb_feed.DEFAULT_CAPACITY > 0
    import inspect

    default = inspect.signature(qdb_feed.feed_table).parameters["capacity"].default
    assert default == qdb_feed.DEFAULT_CAPACITY


# ---- QueryCore: snapshots of a view / LATEST ON; publish only what changed -----------------


class FakeView:
    def __init__(self):
        self.rows = {}  # (exchange, symbol) -> (notional, trades)
        self.calls = 0

    def query(self, sql):
        self.calls += 1
        keys = sorted(self.rows)
        return pa.table(
            {
                "exchange": [k[0] for k in keys],
                "symbol": [k[1] for k in keys],
                "notional": [self.rows[k][0] for k in keys],
                "trades": [self.rows[k][1] for k in keys],
                "bids": [[[1.0, 2.0], [3.0, 4.0]] for _ in keys],
            }
        )


def make_query(view, out):
    return QueryCore(
        "SELECT ...", view.query, lambda t: out.append(t), key_columns=("exchange", "symbol")
    )


def test_query_feed_publishes_everything_first_then_only_changes():
    view, out = FakeView(), []
    view.rows = {("DERIBIT", "BTC"): (100.0, 3), ("OKX", "BTC"): (50.0, 1)}
    core = make_query(view, out)
    assert core.step() == 2
    assert core.step() == 0  # unchanged: nothing ticks downstream
    view.rows[("OKX", "BTC")] = (75.0, 2)  # the open bucket was rewritten
    assert core.step() == 1
    changed = out[-1]
    assert changed.column("exchange").to_pylist() == ["OKX"]
    assert changed.column("notional").to_pylist() == [75.0]


def test_query_feed_new_keys_are_published_and_removed_keys_forgotten():
    view, out = FakeView(), []
    view.rows = {("A", "x"): (1.0, 1)}
    core = make_query(view, out)
    core.step()
    view.rows = {("A", "x"): (1.0, 1), ("B", "x"): (2.0, 1)}
    assert core.step() == 1
    view.rows = {("B", "x"): (2.0, 1)}  # A left the window
    assert core.step() == 0
    view.rows = {("A", "x"): (1.0, 1), ("B", "x"): (2.0, 1)}  # and came back: republished
    assert core.step() == 1


def test_query_feed_handles_array_columns_as_values():
    view, out = FakeView(), []
    view.rows = {("A", "x"): (1.0, 1)}
    core = make_query(view, out)
    assert core.step() == 1 and core.step() == 0


def test_query_feed_ignores_empty_results():
    view, out = FakeView(), []
    core = make_query(view, out)
    assert core.step() == 0 and out == []
