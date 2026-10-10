"""
QuestDB backend for Deephaven's TableDataService: QuestDB keeps the data, Deephaven pages it in.

    from qdb_backend import create_live_table
    trades = create_live_table("trades")                    # whole table, ticking
    books = create_live_table("orderbooks", history_seconds=600)

How rows stay correct. TableDataService addresses rows by position and fetches each column
lazily, at different moments, and may re-read pages later; the size it is told may only grow.
QuestDB orders rows by their (exchange) timestamp, so a row that arrives late is inserted in the
middle and shifts every later position: with plain `ORDER BY timestamp LIMIT lo,hi` paging,
0.9% of live rows had columns glued from different rows. So the table exposes only a *settled
prefix* (see qdb_prefix.py):

    rows with  start <= timestamp < watermark,  watermark = min(max(timestamp), now) - settle

ordered by timestamp plus a total tiebreak (the DEDUP upsert keys, then every other column).
The watermark only advances, positions below the published size never move, and every column
of a page is served from one query (qdb_pagecache). Rows therefore appear `settle_seconds`
after their timestamp (plus up to one poll). A row arriving later than that is not added (the
table cannot retract) but is counted (`backend.prefix.late_rows`) and logged; a published row
that disappears (TTL dropping its partition) is served as a null row (`missing_rows`).
Neither can shift other rows: each ~1 s segment keeps its frozen row count.

API: create_live_table, set_verbose, stop_monitoring, get_backend.
"""

import datetime
import threading
import time

import pyarrow as pa
import qdb_pagecache
import qdb_prefix
from deephaven.experimental.table_data_service import (
    TableDataService,
    TableDataServiceBackend,
    TableKey,
    TableLocationKey,
)
from deephaven.liveness_scope import LivenessScope

# =============================================================================
#  Configuration
# =============================================================================

DEFAULT_ORDER_BY_COL = "timestamp"
DEFAULT_PAGE_SIZE = 64_000
DEFAULT_TRANSPORT = "qwp"
# How far the watermark trails min(max(timestamp), now). Measured on the five live feeds (see
# CLAUDE.md): p99.9 of rows land < 1 s behind, the slowest seen ~1.3 s.
DEFAULT_SETTLE_SECONDS = 3.0
POLL_SECONDS = 0.05

# =============================================================================
#  Global registry: one backend per table, survives re-running a notebook
# =============================================================================

if "_GLOBAL_BACKENDS_BY_TABLE" not in globals():
    _GLOBAL_BACKENDS_BY_TABLE = {}  # table_name -> backend instance
    _GLOBAL_BACKEND_LOCK = threading.Lock()


class QuestDBTableKey(TableKey):
    """TableKey for a QuestDB table"""

    def __init__(self, table_name: str):
        self.table_name = table_name

    def __hash__(self):
        return hash(self.table_name)

    def __eq__(self, other):
        return isinstance(other, QuestDBTableKey) and self.table_name == other.table_name

    def __repr__(self):
        return f"QuestDBTableKey({self.table_name!r})"


class QuestDBTableLocationKey(TableLocationKey):
    """TableLocationKey for QuestDB (single location, no partitioning)"""

    def __init__(self, location_id: str = "main"):
        self.location_id = location_id

    def __hash__(self):
        return hash(self.location_id)

    def __eq__(self, other):
        return isinstance(other, QuestDBTableLocationKey) and self.location_id == other.location_id

    def __repr__(self):
        return f"QuestDBTableLocationKey({self.location_id!r})"


SINGLE_LOCATION_KEY = QuestDBTableLocationKey("main")

# =============================================================================
#  Transports: QWP (Arrow over WebSocket) or Postgres wire
# =============================================================================


class _QwpSource:
    """Reads over QWP: columnar Arrow, nanosecond timestamps, DOUBLE[][] as double[][]."""

    def __init__(self):
        import qdb_qwp

        self._reader = qdb_qwp.QwpReader(flatten_arrays=False)

    def schema(self, table):
        return self._reader.schema(table)

    def rows(self, sql):
        return [tuple(r.values()) for r in self._reader.arrow(sql).to_pylist()]

    def table(self, sql, schema):
        return self._reader.page_sql(sql)

    def close(self):
        self._reader.close()


class _PgSource:
    """Reads over the Postgres wire (psycopg2 via SQLAlchemy); arrays arrive as strings."""

    _TYPES = {
        "TIMESTAMP": pa.timestamp("us", tz="UTC"),
        "TIMESTAMP_NS": pa.timestamp("us", tz="UTC"),
        "DOUBLE": pa.float64(),
        "FLOAT": pa.float32(),
        "INT": pa.int32(),
        "LONG": pa.int64(),
        "SHORT": pa.int16(),
        "BYTE": pa.int8(),
        "BOOLEAN": pa.bool_(),
    }

    def rows(self, sql):
        import qdb

        with qdb.get_engine().connect() as conn:
            with conn.connection.cursor() as cur:
                cur.execute(sql)
                return cur.fetchall()

    def schema(self, table):
        cols = self.rows(f"SELECT \"column\", type FROM table_columns('{table}')")
        if not cols:
            raise RuntimeError(f"Table {table!r} not found or no columns")
        return pa.schema([pa.field(c, self._TYPES.get(t, pa.string())) for c, t in cols])

    def table(self, sql, schema):
        rows = self.rows(sql)
        arrays = {}
        for i, field in enumerate(schema):
            values = [r[i] for r in rows]
            if pa.types.is_timestamp(field.type):
                values = [
                    v.replace(tzinfo=datetime.timezone.utc)
                    if isinstance(v, datetime.datetime) and v.tzinfo is None
                    else v
                    for v in values
                ]
            elif pa.types.is_floating(field.type):
                values = [float(v) if v is not None else None for v in values]
            elif pa.types.is_string(field.type):
                values = [str(v) if v is not None else None for v in values]
            arrays[field.name] = pa.array(values, type=field.type)
        return pa.table(arrays)

    def close(self):
        pass


# =============================================================================
#  Backend
# =============================================================================


class QuestDBBackend(TableDataServiceBackend):
    """One QuestDB table -> one Deephaven table key, single location, settled-prefix paging."""

    def __init__(
        self,
        table_name: str,
        order_by_col: str = DEFAULT_ORDER_BY_COL,
        verbose: bool = False,
        transport: str = DEFAULT_TRANSPORT,
        settle_seconds: float = DEFAULT_SETTLE_SECONDS,
        history_seconds: float = None,
    ):
        super().__init__()
        if transport not in ("pg", "qwp"):
            raise ValueError(f"transport must be 'pg' or 'qwp', got {transport!r}")
        self._table_name = table_name
        self._order_by_col = order_by_col
        self._verbose = verbose
        self._transport = transport
        self.settle_seconds = settle_seconds
        self.history_seconds = history_seconds
        self._source = _QwpSource() if transport == "qwp" else _PgSource()
        self._lock = threading.RLock()
        self._schema = None
        self.prefix = None  # qdb_prefix.StablePrefix, created on first use
        self._subscribers = {}  # id -> size_cb
        self._thread = None
        self._stop = threading.Event()
        self._pages = qdb_pagecache.PageCache()
        self.null_rows_served = 0
        print(
            f"[Backend] new QuestDBBackend table='{table_name}' transport={transport} "
            f"settle={settle_seconds}s history={'all' if history_seconds is None else history_seconds}"
        )

    # ---- lifecycle -------------------------------------------------------------------

    def set_verbose(self, verbose: bool):
        self._verbose = verbose
        print(f"[Backend] verbose {'on' if verbose else 'off'} for '{self._table_name}'")

    def stop(self):
        """Stop the poll thread (tables stop ticking; their rows stay readable)."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None
        with self._lock:
            self._subscribers.clear()

    def cleanup(self):
        self.stop()
        with _GLOBAL_BACKEND_LOCK:
            if _GLOBAL_BACKENDS_BY_TABLE.get(self._table_name) is self:
                del _GLOBAL_BACKENDS_BY_TABLE[self._table_name]

    @classmethod
    def get_or_create(cls, table_name: str, **kwargs):
        """The table's backend, created on first use. Re-running a notebook reuses it, so
        its positions (start, watermark history) stay valid for tables created earlier."""
        with _GLOBAL_BACKEND_LOCK:
            existing = _GLOBAL_BACKENDS_BY_TABLE.get(table_name)
            if existing is not None:
                for name, value in kwargs.items():
                    current = getattr(existing, "_" + name, getattr(existing, name, value))
                    if value is not None and current != value:
                        print(
                            f"[Backend] reusing '{table_name}' backend with {name}={current!r} "
                            f"(requested {value!r}); call stop_monitoring() first to change it"
                        )
                return existing
            backend = cls(table_name=table_name, **kwargs)
            _GLOBAL_BACKENDS_BY_TABLE[table_name] = backend
            return backend

    @classmethod
    def cleanup_all(cls):
        with _GLOBAL_BACKEND_LOCK:
            backends = list(_GLOBAL_BACKENDS_BY_TABLE.values())
        for backend in backends:
            backend.cleanup()
        print(f"[Backend] stopped {len(backends)} backend(s)")

    # ---- metadata and the settled prefix ---------------------------------------------

    def _schema_for(self):
        if self._schema is None:
            self._schema = self._source.schema(self._table_name)
        return self._schema

    def _ensure_prefix(self):
        with self._lock:
            if self.prefix is None:
                self._init_prefix()
            return self.prefix

    def _init_prefix(self):
        t, ts = self._table_name, self._order_by_col
        cols = self._source.rows(f"SELECT \"column\", type, upsertKey FROM table_columns('{t}')")
        types = {c: typ for c, typ, _ in cols}
        unit = "ns" if types.get(ts) == "TIMESTAMP_NS" else "us"
        self._cast = "timestamp_ns" if unit == "ns" else "timestamp"
        arrays = [c for c, typ, _ in cols if "[]" in typ]
        upsert = [c for c, _, key in cols if key]
        self._order = qdb_prefix.order_by([c for c, _, _ in cols], upsert, ts, arrays)
        # array columns sort by their text, computed in ORDER BY only (never transferred)
        self._order_sql = ", ".join(
            f"cast({c} as varchar)" if c in arrays else c for c in self._order
        )
        schema = self._schema_for()
        self._select_sql = ", ".join(schema.names) + f", cast({ts} as long) __ticks"

        ttl = self._source.rows(f"SELECT ttlValue, ttlUnit FROM tables() WHERE table_name = '{t}'")
        if self.history_seconds is None:
            oldest = self._source.rows(f"SELECT cast(min({ts}) as long) FROM {t}")[0][0]
            start = (
                oldest if oldest is not None else time.time_ns() // (1 if unit == "ns" else 1000)
            )
        else:
            tps = qdb_prefix.TICKS_PER_SECOND[unit]
            start = (time.time_ns() * tps // 1_000_000_000) - int(self.history_seconds * tps)

        self.prefix = qdb_prefix.StablePrefix(
            count=self._count,
            max_ts=self._max_ts,
            buckets=self._buckets,
            start=start,
            settle_seconds=self.settle_seconds,
            unit=unit,
            log=lambda msg: print(f"[Backend:{t}] {msg}"),
        )
        size = self.prefix.initialize()
        print(
            f"[Backend:{t}] {size:,} settled rows, order by {', '.join(self._order)}"
            + (
                f"; TTL {ttl[0][0]} {ttl[0][1]}: rows older than that turn into null rows on re-read"
                if ttl and ttl[0][0]
                else ""
            )
        )

    def _lit(self, ticks):
        return f"cast({int(ticks)} as {self._cast})"

    def _range(self, lo, hi):
        ts = self._order_by_col
        return f"{ts} >= {self._lit(lo)} AND {ts} < {self._lit(hi)}"

    def _count(self, lo, hi):
        return int(
            self._source.rows(
                f"SELECT count() FROM {self._table_name} WHERE {self._range(lo, hi)}"
            )[0][0]
        )

    def _max_ts(self):
        return self._source.rows(
            f"SELECT cast(max({self._order_by_col}) as long) FROM {self._table_name}"
        )[0][0]

    def _buckets(self, lo, hi):
        ts = self._order_by_col
        return self._source.rows(
            f"SELECT cast({ts} as long), n FROM (SELECT {ts}, count() n FROM {self._table_name} "
            f"WHERE {self._range(lo, hi)} SAMPLE BY 1s) ORDER BY 1"
        )

    # ---- TableDataServiceBackend: schema and location ---------------------------------

    def table_schema(self, table_key, schema_cb, failure_cb):
        try:
            schema_cb(self._schema_for(), None)
        except Exception as e:
            failure_cb(e)

    def table_locations(self, table_key, location_cb, success_cb, failure_cb):
        try:
            location_cb(SINGLE_LOCATION_KEY, None)
            success_cb()
        except Exception as e:
            failure_cb(e)

    def subscribe_to_table_locations(self, table_key, location_cb, success_cb, failure_cb):
        self.table_locations(table_key, location_cb, success_cb, failure_cb)
        return lambda: None

    # ---- size -----------------------------------------------------------------------------

    def table_location_size(self, table_key, table_location_key, size_cb, failure_cb):
        """Static size, and Deephaven's refresh(): always the published prefix size."""
        try:
            size_cb(self._ensure_prefix().size)
        except Exception as e:
            failure_cb(e)

    def subscribe_to_table_location_size(
        self, table_key, table_location_key, size_cb, success_cb, failure_cb
    ):
        try:
            prefix = self._ensure_prefix()
            size_cb(prefix.size)
            success_cb()
        except Exception as e:
            failure_cb(e)
            return lambda: None
        token = object()
        with self._lock:
            self._subscribers[token] = size_cb
            if self._thread is None or not self._thread.is_alive():
                self._stop = threading.Event()
                self._thread = threading.Thread(
                    target=self._poll_loop,
                    args=(self._stop,),
                    daemon=True,
                    name=f"QuestDB-Monitor-{self._table_name}",
                )
                self._thread.start()

        def unsubscribe():
            with self._lock:
                self._subscribers.pop(token, None)

        return unsubscribe

    def _poll_loop(self, stop):
        t = self._table_name
        print(f"[Backend:{t}] monitoring, size {self.prefix.size:,}")
        while not stop.is_set():
            time.sleep(POLL_SECONDS)
            try:
                with self._lock:
                    size = self.prefix.poll()
                    callbacks = list(self._subscribers.values())
                if size is None:
                    continue
                if self._verbose:
                    print(f"[Backend:{t}] size {size:,} watermark {self.prefix.watermark}")
                for cb in callbacks:
                    cb(size)
            except Exception as e:  # keep ticking through transient QuestDB errors
                print(f"[Backend:{t}] poll failed: {type(e).__name__}: {e}")
                stop.wait(2.0)

    # ---- column values ---------------------------------------------------------------

    def _load_page(self, offset, max_rows):
        """Positions [offset, min(offset + max_rows, size)) of every column, one query."""
        with self._lock:
            prefix = self.prefix
            hi = min(offset + max_rows, prefix.size)
            base, bounds, counts = prefix.locate(offset, hi)
        started = time.perf_counter()
        schema = self._schema_for()
        sql = (
            f"SELECT {self._select_sql} FROM {self._table_name} "
            f"WHERE {self._range(bounds[0], bounds[-1])} ORDER BY {self._order_sql}"
        )
        fetched = self._source.table(sql, schema.append(pa.field("__ticks", pa.int64())))
        ticks = fetched.column("__ticks").to_pylist()
        idx, extra, missing = qdb_prefix.take_indices(ticks, bounds, counts)
        page = fetched.select(schema.names).take(pa.array(idx, pa.int64()))
        page = page.slice(offset - base, hi - offset)
        if missing:
            self.null_rows_served += sum(1 for i in idx[offset - base : hi - base] if i is None)
        if self._verbose or missing or extra:
            print(
                f"[Backend:{self._table_name}] page {offset:,}+{hi - offset:,}: fetched "
                f"{fetched.num_rows:,} rows in {(time.perf_counter() - started) * 1000:.1f} ms"
                + (f", {extra} late row(s) left out" if extra else "")
                + (f", {missing} vanished row(s) served as nulls" if missing else "")
            )
        return page.cast(schema)

    def column_values(
        self,
        table_key,
        table_location_key,
        col,
        offset,
        min_rows,
        max_rows,
        values_cb,
        failure_cb,
    ):
        """Deephaven asks per column; all columns of a page come from one cached query."""
        try:
            page = self._pages.get(
                (offset, max_rows), min_rows, lambda: self._load_page(offset, max_rows)
            )
            values_cb(pa.table({col: page.column(col)}))
        except Exception as e:
            failure_cb(e)


# =============================================================================
#  Convenience functions
# =============================================================================


def create_live_table(
    table_name: str,
    order_by_col: str = DEFAULT_ORDER_BY_COL,
    page_size: int = DEFAULT_PAGE_SIZE,
    refreshing: bool = True,
    verbose: bool = False,
    use_liveness_scope: bool = True,
    transport: str = DEFAULT_TRANSPORT,
    settle_seconds: float = DEFAULT_SETTLE_SECONDS,
    history_seconds: float = None,
):
    """A Deephaven table backed by a QuestDB table through TableDataService.

    Rows appear once settled: `settle_seconds` after their timestamp (default 3 s), so every
    row is exact and positions never move (module docstring). Row order is by timestamp;
    sort explicitly for display.

    Args:
        table_name: QuestDB table (must have a designated timestamp `order_by_col`)
        page_size: rows per Deephaven page (default 64,000)
        refreshing: tick as new rows settle (default True)
        verbose: log every size change and page load
        transport: "qwp" (default; Arrow over QuestDB's WebSocket protocol, DOUBLE[][] as
            double[][]) or "pg" (Postgres wire; arrays as strings, microsecond timestamps)
        settle_seconds: how far behind min(max(timestamp), now) the watermark trails
        history_seconds: rows older than this at creation are not included (None: the whole
            table). A table with a TTL loses old rows; see the module docstring.

    The backend is shared per table (re-running a notebook reuses it); `get_backend(name)`
    exposes its counters: prefix.size, prefix.watermark, prefix.late_rows, prefix.missing_rows.
    """
    backend = QuestDBBackend.get_or_create(
        table_name,
        order_by_col=order_by_col,
        verbose=verbose,
        transport=transport,
        settle_seconds=settle_seconds,
        history_seconds=history_seconds,
    )
    tds = TableDataService(backend=backend, page_size=page_size)
    table_key = QuestDBTableKey(table_name)
    if use_liveness_scope:
        scope = LivenessScope()
        with scope.open():
            return tds.make_table(table_key, refreshing=refreshing)
    return tds.make_table(table_key, refreshing=refreshing)


def get_backend(table_name: str):
    with _GLOBAL_BACKEND_LOCK:
        return _GLOBAL_BACKENDS_BY_TABLE.get(table_name)


def set_verbose(table_name: str, verbose: bool):
    """Enable or disable verbose logging for one table's backend."""
    backend = get_backend(table_name)
    if backend is None:
        print(f"[Backend] No backend found for table '{table_name}'")
    else:
        backend.set_verbose(verbose)


def stop_monitoring():
    """Stop all QuestDB monitoring threads; tables stop ticking. Re-run create_live_table()
    (a fresh backend) to start again."""
    QuestDBBackend.cleanup_all()
