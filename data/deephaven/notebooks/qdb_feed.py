"""
Append-only ticking Deephaven tables fed from QuestDB.

How it differs from the TableDataService live table (qdb_backend): that one addresses rows by
position, so it can only show a settled prefix of the table (rows a few seconds old, see
qdb_prefix) to keep positions from moving. This module needs no positions: a background thread
polls QuestDB for rows at or after a rolling watermark, de-duplicates them, and publishes each
new row exactly once through a Deephaven table publisher. Rows are owned by the engine and never
move, so rows appear within a poll (0.25 s) of becoming visible. A row that shows up late
(still within `overlap_seconds` of the watermark) is simply appended when it is first seen;
use the timestamp column, not row position, for ordering.

    from qdb_feed import feed_table
    trades = feed_table("trades", history_seconds=900)                       # ring of 100k rows
    liq = feed_table("perps_liquidity", history_seconds=3600, capacity=50_000)
    audit = feed_table("trades", capacity=None)           # unbounded append-only: opt in

A second kind of feed, `feed_query`, snapshots the result of any SQL (typically a
materialized view or a LATEST ON query) every poll and publishes only the rows whose values
changed. The result is a small state table, one row per key, that Deephaven updates in place.
That is how the dashboard reads QuestDB materialized views: a view rewrites its open bucket
as trades arrive, which an append-only, position-addressed table cannot represent.

    window = feed_query(
        "SELECT exchange, symbol, sum(notional_usd) notional FROM perps_trades_10s "
        "WHERE timestamp > dateadd('m', -5, now()) GROUP BY exchange, symbol",
        key_columns=["exchange", "symbol"])

Limits: rows that become visible more than `overlap_seconds` after their timestamp are not
seen by `feed_table` (WAL apply lag here is milliseconds); nothing is ever deleted downstream.
"""

import datetime
import threading
import time

DEFAULT_POLL_SECONDS = 0.25
DEFAULT_OVERLAP_SECONDS = 3.0
# Rows kept in the ring table. An unbounded append-only table grows for as long as the
# process lives, so a bounded ring is the default. 100k rows is ~25 min of all trades here
# and ~35 min of one instrument's 1 Hz liquidity rows; size it to the history you need.
DEFAULT_CAPACITY = 100_000


# --------------------------------------------------------------------------------------
# pure polling / de-duplication logic (no Deephaven imports, unit tested)
# --------------------------------------------------------------------------------------


def _iso_micros(ts_ns: int) -> str:
    """Nanosecond epoch -> ISO string with microseconds, for a QuestDB literal."""
    seconds, micros = divmod(ts_ns // 1000, 1_000_000)
    dt = datetime.datetime.fromtimestamp(seconds, tz=datetime.timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{micros:06d}Z"


class FeedCore:
    """Decides which rows are new. Collaborators are injected.

    query(sql) -> pyarrow.Table with the timestamp column named `ts_column`
    publish(table) -> None (called with a pyarrow.Table of rows never published before)
    key_columns: columns that identify a row (QuestDB's upsert keys when it has them)
    """

    def __init__(
        self,
        table,
        query,
        publish,
        key_columns,
        ts_column="timestamp",
        history_seconds=900,
        overlap_seconds=DEFAULT_OVERLAP_SECONDS,
        where=None,
        clock=time.time,
    ):
        self.table = table
        self._query = query
        self._publish = publish
        self.key_columns = tuple(key_columns)
        self.ts_column = ts_column
        self.history_seconds = history_seconds
        self.overlap_ns = int(overlap_seconds * 1e9)
        self.where = where
        self._clock = clock
        self.watermark_ns = None
        # key -> timestamp (ns) for rows at or after watermark - 2 * overlap
        self._seen = {}
        self.rows_published = 0

    # ---- SQL
    def _select(self, condition):
        extra = f" AND ({self.where})" if self.where else ""
        return f"SELECT * FROM {self.table} WHERE {condition}{extra} ORDER BY {self.ts_column}"

    def initial_sql(self):
        since_ns = int((self._clock() - self.history_seconds) * 1e9)
        return self._select(f"{self.ts_column} >= '{_iso_micros(since_ns)}'")

    def poll_sql(self):
        return self._select(
            f"{self.ts_column} >= '{_iso_micros(self.watermark_ns - self.overlap_ns)}'"
        )

    # ---- filtering
    def _timestamps_ns(self, rows):
        column = rows.column(self.ts_column)
        # pyarrow timestamps: cast to int64 nanoseconds regardless of the stored unit
        import pyarrow as pa

        ns = column.cast(pa.timestamp("ns", tz=column.type.tz)).cast(pa.int64())
        return ns.to_pylist()

    @staticmethod
    def _values(rows, name):
        """Column as Python values; timestamps become int nanoseconds (no pandas needed)."""
        import pyarrow as pa

        column = rows.column(name)
        if pa.types.is_timestamp(column.type):
            column = column.cast(pa.timestamp("ns", tz=column.type.tz)).cast(pa.int64())
        return column.to_pylist()

    def _filter_new(self, rows):
        """Rows not published before, remembering them. Returns a pyarrow Table (maybe empty)."""
        import pyarrow as pa

        stamps = self._timestamps_ns(rows)
        key_columns = [self._values(rows, c) for c in self.key_columns]
        keep = []
        for i, stamp in enumerate(stamps):
            key = tuple(col[i] for col in key_columns)
            if key in self._seen:
                keep.append(False)
            else:
                self._seen[key] = stamp
                keep.append(True)
        return rows.filter(pa.array(keep)), stamps

    def _prune(self):
        floor = self.watermark_ns - 2 * self.overlap_ns
        if len(self._seen) > 1000:  # cheap guard: pruning is O(n)
            self._seen = {k: v for k, v in self._seen.items() if v >= floor}

    # ---- steps
    def step(self):
        """One poll (the first call loads the history). Returns the number of rows published."""
        sql = self.initial_sql() if self.watermark_ns is None else self.poll_sql()
        rows = self._query(sql)
        if rows.num_rows == 0:
            return 0
        new_rows, stamps = self._filter_new(rows)
        newest = max(stamps)
        self.watermark_ns = newest if self.watermark_ns is None else max(self.watermark_ns, newest)
        self._prune()
        if new_rows.num_rows:
            self._publish(new_rows)
            self.rows_published += new_rows.num_rows
        return new_rows.num_rows


def _freeze(value):
    """Hashable form of a column value (lists become tuples)."""
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    return value


class QueryCore:
    """Publishes only the rows of a query result that are new or whose values changed.

    The SQL is re-run on every step and returns the *current* state (a view, a LATEST ON, a
    rolling aggregate). Rows are identified by `key_columns`; a key seen before with identical
    values is skipped, so downstream tables only tick when something actually changed.
    """

    def __init__(self, sql, query, publish, key_columns):
        self.sql = sql
        self._query = query
        self._publish = publish
        self.key_columns = tuple(key_columns)
        self._state = {}  # key -> frozen row values
        self.rows_published = 0

    def step(self):
        rows = self._query(self.sql)
        if rows.num_rows == 0:
            return 0
        import pyarrow as pa

        names = rows.column_names
        columns = [FeedCore._values(rows, n) for n in names]
        key_positions = [names.index(c) for c in self.key_columns]
        changed, current = [], {}
        for i in range(rows.num_rows):
            values = tuple(_freeze(col[i]) for col in columns)
            key = tuple(values[p] for p in key_positions)
            current[key] = values
            changed.append(self._state.get(key) != values)
        self._state = current  # keys that left the result are forgotten (they may come back)
        count = sum(changed)
        if count:
            self._publish(rows.filter(pa.array(changed)))
            self.rows_published += count
        return count


# --------------------------------------------------------------------------------------
# Deephaven wiring
# --------------------------------------------------------------------------------------


def _dummy_value(arrow_type):
    import pyarrow as pa

    if pa.types.is_timestamp(arrow_type):
        return 0
    if pa.types.is_floating(arrow_type):
        return 0.0
    if pa.types.is_integer(arrow_type):
        return 0
    if pa.types.is_boolean(arrow_type):
        return False
    if pa.types.is_list(arrow_type):
        return [_dummy_value(arrow_type.value_type)]
    return ""


def _definition_for(schema):
    """Deephaven column definitions for an Arrow schema, via a one-row prototype table."""
    import pyarrow as pa
    from deephaven import arrow as dharrow

    proto = pa.table({f.name: pa.array([_dummy_value(f.type)], type=f.type) for f in schema})
    return dharrow.to_table(proto).definition


def feed_table(
    table_name,
    history_seconds=900,
    poll_seconds=DEFAULT_POLL_SECONDS,
    overlap_seconds=DEFAULT_OVERLAP_SECONDS,
    where=None,
    capacity=DEFAULT_CAPACITY,
    key_columns=None,
    conf=None,
    name=None,
):
    """A ticking, append-only Deephaven table of recent rows from a QuestDB table.

    history_seconds: how much existing history to load at start
    capacity: keep only the latest N rows (a Deephaven ring_table) so memory stays bounded;
        None makes the table unbounded append-only, which grows for as long as the process runs.
        Memory is capacity x row size, so tables with array columns need a smaller capacity.
    key_columns: columns identifying a row; default is QuestDB's DEDUP upsert keys, else every
        non-array column
    where: extra SQL condition (e.g. "contract_type <> 'spot'")
    """
    import qdb_qwp
    from deephaven import arrow as dharrow
    from deephaven.execution_context import get_exec_ctx
    from deephaven.stream import blink_to_append_only
    from deephaven.stream.table_publisher import table_publisher
    from deephaven.table_factory import ring_table

    reader = qdb_qwp.QwpReader(conf or qdb_qwp.DEFAULT_CONF)
    schema = reader.schema(table_name)
    if key_columns is None:
        key_columns = _default_key_columns(reader, table_name, schema)

    blink, publisher = table_publisher(
        name or f"qdb_feed_{table_name}", _definition_for(schema), chunk_size=8192
    )
    context = get_exec_ctx()

    def query(sql):
        return reader.page_sql(sql)

    def publish(rows):
        with context:
            publisher.add(dharrow.to_table(rows))

    core = FeedCore(
        table_name,
        query,
        publish,
        key_columns,
        history_seconds=history_seconds,
        overlap_seconds=overlap_seconds,
        where=where,
    )

    def run():
        while True:
            try:
                core.step()
            except Exception as e:  # keep polling through transient QuestDB errors
                print(f"[qdb_feed:{table_name}] poll failed: {type(e).__name__}: {e}")
                time.sleep(2.0)
            time.sleep(poll_seconds)

    thread = threading.Thread(target=run, name=f"qdb-feed-{table_name}", daemon=True)
    # load the history synchronously so the returned table is populated, then keep polling
    core.step()
    thread.start()

    with context:
        result = ring_table(blink, capacity) if capacity else blink_to_append_only(blink)
    result.feed = core  # introspection: result.feed.rows_published, .watermark_ns
    return result


def feed_query(
    sql,
    key_columns,
    poll_seconds=1.0,
    capacity=None,
    conf=None,
    name="qdb_query",
    first_rows_timeout=10.0,
):
    """A ticking state table (one row per key) holding the current result of `sql`.

    The SQL is re-run every `poll_seconds`; only new or changed rows are published, and the
    table keeps the latest version per key. capacity bounds the number of row *versions* kept
    (use it for time series whose keys keep changing, e.g. a rolling window of buckets).
    Blocks until the query returns its first rows (up to `first_rows_timeout` seconds) because
    the column types, including 2-D array columns, are taken from real data.
    """
    import qdb_qwp
    from deephaven import arrow as dharrow
    from deephaven.execution_context import get_exec_ctx
    from deephaven.stream.table_publisher import table_publisher
    from deephaven.table_factory import ring_table

    reader = qdb_qwp.QwpReader(conf or qdb_qwp.DEFAULT_CONF)

    deadline = time.time() + first_rows_timeout
    first = reader.page_sql(sql)
    while first.num_rows == 0 and time.time() < deadline:
        time.sleep(0.25)
        first = reader.page_sql(sql)
    if first.num_rows == 0:
        raise RuntimeError(f"query returned no rows within {first_rows_timeout}s: {sql[:120]}")

    blink, publisher = table_publisher(
        name, dharrow.to_table(first.slice(0, 1)).definition, chunk_size=2048
    )
    context = get_exec_ctx()

    def publish(rows):
        with context:
            publisher.add(dharrow.to_table(rows))

    core = QueryCore(sql, reader.page_sql, publish, key_columns)

    def run():
        while True:
            time.sleep(poll_seconds)
            try:
                core.step()
            except Exception as e:  # keep polling through transient QuestDB errors
                print(f"[qdb_feed:{name}] poll failed: {type(e).__name__}: {e}")
                time.sleep(2.0)

    core.step()
    threading.Thread(target=run, name=f"qdb-query-{name}", daemon=True).start()

    with context:
        versions = ring_table(blink, capacity) if capacity else blink
        state = versions.last_by(list(key_columns))
    state.feed = core
    return state


def _default_key_columns(reader, table_name, schema):
    import pyarrow as pa

    rows = reader.arrow(
        f"SELECT \"column\" AS c, upsertKey AS k FROM table_columns('{table_name}')"
    ).to_pylist()
    upsert = [r["c"] for r in rows if r["k"]]
    if upsert:
        return upsert
    return [f.name for f in schema if not pa.types.is_list(f.type)]
