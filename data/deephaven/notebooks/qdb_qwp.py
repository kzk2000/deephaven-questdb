"""
QuestDB reads over QWP (QuestDB's WebSocket wire protocol) as pyarrow tables.

Used by qdb_backend.QuestDBBackend when created with transport="qwp". Compared with the
Postgres-wire path (psycopg2 + ROW_NUMBER() paging), measured on a 1M-row table in the
Deephaven container:

    64k-row page   PG + ROW_NUMBER  ~90-120 ms   QWP + LIMIT lo,hi  ~3-6 ms
    schema         information_schema ~2 ms      SELECT ... LIMIT 0  ~0.4 ms
    row count      wal_transactions   ~2 ms      count()             ~0.4 ms

Results arrive as columnar Arrow batches (no per-row Python conversion) with nanosecond
timestamps, so TIMESTAMP_NS columns are exact end to end.

Type normalization (`normalize`) keeps the Arrow table directly consumable by Deephaven:
    - dictionary-encoded SYMBOL columns are decoded to plain strings
    - timestamps stay timestamp[ns, tz=UTC]
    - DOUBLE[][] (list<list<double>>) cannot be consumed by Deephaven's TableDataService (it
      declares double[] but receives double[][]), so flatten_arrays=True renders such columns
      as JSON strings, the representation the Postgres-wire path produced
"""

import json
import threading

import pyarrow as pa
import questdb

DEFAULT_CONF = "ws::addr=questdb:9000;"


def normalize(table: pa.Table, flatten_arrays: bool = False) -> pa.Table:
    """Make a QWP result Arrow table Deephaven-friendly (see module docstring)."""
    columns = []
    for field, col in zip(table.schema, table.columns):
        t = field.type
        if pa.types.is_dictionary(t):
            col = col.cast(t.value_type)
        elif flatten_arrays and pa.types.is_list(t):
            col = pa.chunked_array(
                [pa.array([json.dumps(v) for v in col.to_pylist()], pa.string())]
            )
        columns.append(col)
    names = table.schema.names
    return pa.table(dict(zip(names, columns)))


class QwpReader:
    """Thread-safe read helper around a questdb QWP connection.

    A single QWP connection runs one query at a time, so calls are serialized with a lock.
    The backend calls this from several Deephaven threads plus its change-watcher thread.
    """

    def __init__(self, conf: str = DEFAULT_CONF, flatten_arrays: bool = False):
        self._conf = conf
        self._flatten_arrays = flatten_arrays
        self._lock = threading.Lock()
        self._db = None

    def _connect(self):
        if self._db is None:
            self._db = questdb.connect(self._conf)
        return self._db

    def close(self):
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None

    def arrow(self, sql: str) -> pa.Table:
        """Run a query and return the raw Arrow table. Reconnects once on a dropped link."""
        with self._lock:
            for attempt in (1, 2):
                try:
                    return self._connect().query(sql).to_arrow()
                except Exception:
                    # drop the (possibly dead) connection and retry once
                    try:
                        if self._db is not None:
                            self._db.close()
                    except Exception:
                        pass
                    self._db = None
                    if attempt == 2:
                        raise

    # --- helpers the backend needs -------------------------------------------------

    def schema(self, table: str) -> pa.Schema:
        """Exact Arrow schema of a table (zero-row query), normalized."""
        return normalize(self.arrow(f"SELECT * FROM {table} LIMIT 0"), self._flatten_arrays).schema

    def row_count(self, table: str) -> int:
        return int(self.arrow(f"SELECT count() AS n FROM {table}").column("n")[0].as_py())

    def page(self, table: str, column: str, offset: int, count: int, order_by: str) -> pa.Table:
        """Rows [offset, offset+count) of one column in `order_by` order, normalized."""
        sql = f"SELECT {column} FROM {table} ORDER BY {order_by} LIMIT {int(offset)},{int(offset) + int(count)}"
        return normalize(self.arrow(sql), self._flatten_arrays)
