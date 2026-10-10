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
    - DOUBLE[][] (list<list<double>>) is kept as a nested list, and `with_deephaven_hints`
      annotates the schema field with deephaven:type="double[][]". Without the hint
      TableDataService infers double[] from the nested list and then rejects the double[][]
      data ("Received incompatible column definition"). flatten_arrays=True is the escape
      hatch that renders such columns as JSON strings instead
"""

import json
import threading

import pyarrow as pa
import questdb

DEFAULT_CONF = "ws::addr=questdb:9000;"

# Arrow primitive -> Deephaven element type name (as used in deephaven:type metadata)
_DH_ELEMENT_TYPES = {
    pa.float64(): "double",
    pa.float32(): "float",
    pa.int64(): "long",
    pa.int32(): "int",
    pa.int16(): "short",
    pa.int8(): "byte",
    pa.bool_(): "boolean",
}


def deephaven_type_hint(arrow_type: pa.DataType):
    """`deephaven:type` for lists nested two or more levels deep (e.g. "double[][]"), else None.

    One-level lists already map correctly (list<double> -> double[]).
    """
    depth = 0
    t = arrow_type
    while pa.types.is_list(t) or pa.types.is_large_list(t) or pa.types.is_fixed_size_list(t):
        depth += 1
        t = t.value_type
    if depth >= 2 and t in _DH_ELEMENT_TYPES:
        return _DH_ELEMENT_TYPES[t] + "[]" * depth
    return None


def apply_array_dimensions(schema: pa.Schema, dims: dict) -> pa.Schema:
    """Rebuild array fields with their true nesting depth.

    A zero-row QWP result reports every array column as list<double>; the dimensionality is
    only visible in actual data. `dims` maps column name -> number of dimensions (from
    table_columns(), e.g. DOUBLE[][] -> 2), which is exact and also works on empty tables.
    """
    fields = []
    for field in schema:
        n = dims.get(field.name, 0)
        t = field.type
        depth = 0
        while pa.types.is_list(t) or pa.types.is_large_list(t):
            depth += 1
            t = t.value_type
        if n > depth >= 1:
            new_type = t
            for _ in range(n):
                new_type = pa.list_(new_type)
            field = pa.field(field.name, new_type, field.nullable, field.metadata)
        fields.append(field)
    return pa.schema(fields)


def with_deephaven_hints(schema: pa.Schema) -> pa.Schema:
    """Attach deephaven:type metadata to nested-list fields so TableDataService types them."""
    fields = []
    for field in schema:
        hint = deephaven_type_hint(field.type)
        if hint is not None:
            field = field.with_metadata({"deephaven:type": hint})
        fields.append(field)
    return pa.schema(fields)


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
        """Exact Arrow schema of a table (zero-row query), normalized and Deephaven-hinted."""
        schema = normalize(
            self.arrow(f"SELECT * FROM {table} LIMIT 0"), self._flatten_arrays
        ).schema
        if self._flatten_arrays:
            return schema
        # a zero-row result cannot tell DOUBLE[] from DOUBLE[][]; table_columns() can
        cols = self.arrow(f"SELECT \"column\" AS c, type AS t FROM table_columns('{table}')")
        dims = {
            c: t.count("[]")
            for c, t in zip(cols.column("c").to_pylist(), cols.column("t").to_pylist())
        }
        return with_deephaven_hints(apply_array_dimensions(schema, dims))

    def row_count(self, table: str) -> int:
        return int(self.arrow(f"SELECT count() AS n FROM {table}").column("n")[0].as_py())

    def page(self, table: str, column: str, offset: int, count: int, order_by: str) -> pa.Table:
        """Rows [offset, offset+count) of one column in `order_by` order, normalized."""
        sql = f"SELECT {column} FROM {table} ORDER BY {order_by} LIMIT {int(offset)},{int(offset) + int(count)}"
        return normalize(self.arrow(sql), self._flatten_arrays)
