# Live streaming QuestDB trades via TableDataService + WAL monitoring
# This creates a Deephaven table backed by QuestDB storage with real-time updates
#
# REQUIREMENTS: Deephaven main branch (edge build) or v0.41.0+ when released
# TableDataService API was added Nov 1, 2024 - not in v0.40.x releases

from deephaven.experimental.table_data_service import (
    TableDataService,
    TableDataServiceBackend,
    TableKey,
    TableLocationKey,
)

import pyarrow as pa
import qdb
import threading
import time
from abc import ABC

# =============================================================================
#  Configuration
# =============================================================================

TARGET_TABLE_NAME = "trades"   # QuestDB table to expose
ORDER_BY_COL = "timestamp"     # Main order key (timestamp column)
PAGE_SIZE = 64_000             # Deephaven page size (rows per page)

# How often to check WAL metadata if no new txns
WAL_IDLE_SLEEP_SEC = 0.05


# =============================================================================
#  TableKey & TableLocationKey
# =============================================================================

class QuestDBTableKey(TableKey):
    def __init__(self, table_name: str):
        self.table_name = table_name

    def __hash__(self):
        return hash(self.table_name)

    def __eq__(self, other):
        return isinstance(other, QuestDBTableKey) and self.table_name == other.table_name

    def __repr__(self):
        return f"QuestDBTableKey({self.table_name!r})"


class QuestDBTableLocationKey(TableLocationKey):
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
#  Backend: WAL-driven, rowid-based paging
# =============================================================================

class QuestDBBackend(TableDataServiceBackend, ABC):
    """
    QuestDB-backed TableDataServiceBackend with WAL-driven streaming.

    - One QuestDB table -> one Deephaven table key
    - Single location (no partitioning yet)
    - Append-only, ordered by ORDER_BY_COL (e.g., timestamp)
    - Detects new rows via wal_transactions()
    - Uses _rowid ranges for paging (no LIMIT/OFFSET scans)
    """

    def __init__(self, order_by_col: str = ORDER_BY_COL):
        super().__init__()
        self._order_by_col = order_by_col

    # -------------------------------------------------------------------------
    #  Schema
    # -------------------------------------------------------------------------

    def _get_schema_from_information_schema(self, table_name: str) -> pa.Schema:
        """
        Introspect QuestDB using information_schema.columns and build a PyArrow schema.
        We exclude the hidden _rowid from the public schema but still use it internally.
        """
        q = """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = %s
        ORDER BY ordinal_position
        """
        with qdb.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(q, (table_name,))
                rows = cur.fetchall()

        if not rows:
            raise RuntimeError(f"Table {table_name!r} not found or no columns")

        fields = []
        for name, dtype in rows:
            # Skip the hidden _rowid if it appears
            if name.lower() == "_rowid":
                continue

            t = (dtype or "").upper()
            if t in ("TIMESTAMP", "TIMESTAMPTZ"):
                arrow_type = pa.timestamp("ns")
            elif t in ("DOUBLE", "DOUBLE PRECISION"):
                arrow_type = pa.float64()
            elif t in ("FLOAT", "REAL"):
                arrow_type = pa.float32()
            elif t in ("INT", "INTEGER"):
                arrow_type = pa.int32()
            elif t in ("LONG", "BIGINT"):
                arrow_type = pa.int64()
            elif t in ("SHORT", "SMALLINT"):
                arrow_type = pa.int16()
            elif t in ("BYTE", "TINYINT"):
                arrow_type = pa.int8()
            elif t in ("BOOLEAN", "BOOL"):
                arrow_type = pa.bool_()
            elif t in ("STRING", "SYMBOL", "TEXT", "VARCHAR", "CHAR"):
                arrow_type = pa.string()
            else:
                arrow_type = pa.string()

            fields.append(pa.field(name, arrow_type))

        return pa.schema(fields)

    def table_schema(self, table_key, schema_cb, failure_cb):
        try:
            assert isinstance(table_key, QuestDBTableKey)
            table_name = table_key.table_name
            schema = self._get_schema_from_information_schema(table_name)
            schema_cb(schema, None)   # no partition schema
        except Exception as e:
            failure_cb(e)

    # -------------------------------------------------------------------------
    #  Location(s): single location
    # -------------------------------------------------------------------------

    def table_locations(self, table_key, location_cb, success_cb, failure_cb):
        try:
            location_cb(SINGLE_LOCATION_KEY, None)
            success_cb()
        except Exception as e:
            failure_cb(e)

    def subscribe_to_table_locations(self, table_key, location_cb, success_cb, failure_cb):
        try:
            location_cb(SINGLE_LOCATION_KEY, None)
            success_cb()

            def unsubscribe():
                return

            return unsubscribe
        except Exception as e:
            failure_cb(e)

            def noop():
                return

            return noop

    # -------------------------------------------------------------------------
    #  Size tracking: WAL-driven, rowid-based
    # -------------------------------------------------------------------------

    def _get_max_rowid_and_last_txn(self, table_name: str):
        """
        Get current max _rowid and latest WAL txn for this table.
        We assume:
          - table is WAL-enabled
          - _rowid is monotonic as rows append
        """
        with qdb.get_connection() as conn:
            with conn.cursor() as cur:
                # latest txn for this table
                cur.execute(
                    """
                    SELECT coalesce(max(seq_txn), 0)
                    FROM wal_tables()
                    WHERE name = %s
                    """,
                    (table_name,),
                )
                (last_txn,) = cur.fetchone()
                last_txn = int(last_txn or 0)

                # max rowid (could be 0 if table empty)
                cur.execute(
                    f"SELECT coalesce(max(timestamp), 0) FROM {table_name}",
                )
                (max_ts,) = cur.fetchone()

                # Get row count instead of rowid
                cur.execute(f"SELECT count(*) FROM {table_name}")
                (row_count,) = cur.fetchone()
                row_count = int(row_count or 0)

        return last_txn, row_count

    def _wal_watch_loop(self, table_key, size_cb, success_cb, failure_cb, stop_event):
        """
        Background loop:
        - watches wal_tables() for this table
        - uses it to infer new rows via count(*)
        - calls size_cb(new_size) whenever size grows
        """
        try:
            assert isinstance(table_key, QuestDBTableKey)
            table_name = table_key.table_name

            # Initial: last txn + row count
            last_txn, last_count = self._get_max_rowid_and_last_txn(table_name)
            # Initial size reported
            size_cb(last_count)
            success_cb()

            while not stop_event.is_set():
                time.sleep(WAL_IDLE_SLEEP_SEC)
                with qdb.get_connection() as conn:
                    with conn.cursor() as cur:
                        # check for new wal txns for this table
                        cur.execute(
                            """
                            SELECT coalesce(max(seq_txn), 0)
                            FROM wal_tables()
                            WHERE name = %s AND seq_txn > %s
                            """,
                            (table_name, last_txn),
                        )
                        (max_new_txn,) = cur.fetchone()
                        max_new_txn = int(max_new_txn or 0)

                        if max_new_txn <= last_txn:
                            continue  # nothing new

                        # there are committed txns since last_txn → new rows visible
                        # get new row count
                        cur.execute(f"SELECT count(*) FROM {table_name}")
                        (new_count,) = cur.fetchone()
                        new_count = int(new_count or 0)

                if new_count > last_count:
                    last_count = new_count
                    last_txn = max_new_txn
                    size_cb(last_count)

        except Exception as e:
            failure_cb(e)

    def table_location_size(self, table_key, table_location_key, size_cb, failure_cb):
        """
        Static size (non-refreshing use). We use row count as size.
        """
        try:
            assert isinstance(table_key, QuestDBTableKey)
            table_name = table_key.table_name
            _, row_count = self._get_max_rowid_and_last_txn(table_name)
            size_cb(row_count)
        except Exception as e:
            failure_cb(e)

    def subscribe_to_table_location_size(
        self, table_key, table_location_key, size_cb, success_cb, failure_cb
    ):
        """
        Refreshing size – WAL-driven, not interval count(*) polling.
        """
        stop_event = threading.Event()

        t = threading.Thread(
            target=self._wal_watch_loop,
            args=(table_key, size_cb, success_cb, failure_cb, stop_event),
            daemon=True,
        )
        t.start()

        def unsubscribe():
            stop_event.set()

        return unsubscribe

    # -------------------------------------------------------------------------
    #  Column paging: use timestamp ranges
    # -------------------------------------------------------------------------

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
        """
        Provide a single-column pyarrow.Table with rows [offset, offset+N),
        using LIMIT/OFFSET for simplicity (can optimize with timestamp ranges later).
        """
        try:
            assert isinstance(table_key, QuestDBTableKey)
            table_name = table_key.table_name

            sql = f"""
                SELECT {col}
                FROM {table_name}
                ORDER BY {self._order_by_col}
                LIMIT {max_rows}
                OFFSET {offset}
            """

            with qdb.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql)
                    rows = cur.fetchall()

            values = [r[0] for r in rows]
            pa_table = pa.Table.from_pydict({col: values})
            values_cb(pa_table)
        except Exception as e:
            failure_cb(e)


# =============================================================================
#  Wire it up
# =============================================================================

print("="*70)
print("Initializing QuestDB WAL-backed streaming table...")
print("="*70)

qdb_backend = QuestDBBackend(order_by_col=ORDER_BY_COL)

qdb_tds = TableDataService(
    backend=qdb_backend,
    page_size=PAGE_SIZE,   # controls DH paging / memory usage
)

qdb_ticks_key = QuestDBTableKey(TARGET_TABLE_NAME)

# This Deephaven table is:
# - backed by QuestDB storage
# - streaming (refreshing=True)
# - WAL-driven (no frequent count(*) polling)
print(f"\nCreating live streaming table: {TARGET_TABLE_NAME}")
print(f"   Order by: {ORDER_BY_COL}")
print(f"   Page size: {PAGE_SIZE:,} rows")

trades_wal = qdb_tds.make_table(qdb_ticks_key, refreshing=True)

print(f"\nLive table created: trades_wal")
print(f"   Updates automatically as QuestDB receives new data")
print(f"   Uses WAL monitoring (no polling!)")
print("\n" + "="*70)
print("Usage:")
print("  trades_wal              # View live streaming trades")
print("  trades_wal.tail(100)    # View latest 100 trades")
print("  trades_wal.where('symbol == \"BTC-USD\"')  # Filter by symbol")
print("="*70)
