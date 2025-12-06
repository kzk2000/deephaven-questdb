# Run this INSIDE the Deephaven server's Python environment (e.g. in a script or appmode file).

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
#  Configuration – EDIT THESE FOR YOUR ENV
# =============================================================================

TARGET_TABLE_NAME = "trades"  # QuestDB table we want to expose
ORDER_BY_COL = "ts"           # main order key in QuestDB (e.g. timestamp column)
PAGE_SIZE = 64_000            # Deephaven page size (rows per page)

# How often to check WAL metadata if no new txns (as a backoff)
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
    - Append-only, ordered by ORDER_BY_COL (e.g., ts)
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
            # you can optionally skip the hidden _rowid if it appears
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
                    SELECT coalesce(max(wal_txn), 0)
                    FROM wal_transactions()
                    WHERE table_name = %s
                    """,
                    (table_name,),
                )
                (last_txn,) = cur.fetchone()
                last_txn = int(last_txn or 0)

                # max rowid (could be 0 if table empty)
                cur.execute(
                    f"SELECT coalesce(max(_rowid), 0) FROM {table_name}",
                )
                (max_rowid,) = cur.fetchone()
                max_rowid = int(max_rowid or 0)

        return last_txn, max_rowid

    def _wal_watch_loop(self, table_key, size_cb, success_cb, failure_cb, stop_event):
        """
        Background loop:
        - watches wal_transactions() for this table
        - uses it to infer new rows via max(_rowid)
        - calls size_cb(new_size) whenever size grows
        """
        try:
            assert isinstance(table_key, QuestDBTableKey)
            table_name = table_key.table_name

            # Initial: last txn + max rowid
            last_txn, last_rowid = self._get_max_rowid_and_last_txn(table_name)
            # Initial size reported as last_rowid (we treat rowid as 1-based count)
            size_cb(last_rowid)
            success_cb()

            while not stop_event.is_set():
                time.sleep(WAL_IDLE_SLEEP_SEC)
                with qdb.get_connection() as conn:
                    with conn.cursor() as cur:
                        # check for new wal txns for this table
                        cur.execute(
                            """
                            SELECT coalesce(max(wal_txn), 0)
                            FROM wal_transactions()
                            WHERE table_name = %s AND wal_txn > %s
                            """,
                            (table_name, last_txn),
                        )
                        (max_new_txn,) = cur.fetchone()
                        max_new_txn = int(max_new_txn or 0)

                        if max_new_txn <= last_txn:
                            continue  # nothing new

                        # there are committed txns since last_txn → new rows visible
                        # get new max rowid
                        cur.execute(
                            f"SELECT coalesce(max(_rowid), 0) FROM {table_name}"
                        )
                        (max_rowid,) = cur.fetchone()
                        max_rowid = int(max_rowid or 0)

                if max_rowid > last_rowid:
                    last_rowid = max_rowid
                    last_txn = max_new_txn
                    size_cb(last_rowid)

        except Exception as e:
            failure_cb(e)

    def table_location_size(self, table_key, table_location_key, size_cb, failure_cb):
        """
        Static size (non-refreshing use). We use last_rowid as size.
        """
        try:
            assert isinstance(table_key, QuestDBTableKey)
            table_name = table_key.table_name
            _, max_rowid = self._get_max_rowid_and_last_txn(table_name)
            size_cb(max_rowid)
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
    #  Column paging: use rowid ranges
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
        using _rowid-based ranges instead of LIMIT/OFFSET.
        Deephaven's offset is 0-based; we assume _rowid is 1-based.
        """
        try:
            assert isinstance(table_key, QuestDBTableKey)
            table_name = table_key.table_name

            # Convert 0-based offset to 1-based rowid start
            rowid_start = offset + 1
            rowid_end = rowid_start + max_rows - 1

            sql = f"""
                SELECT {col}
                FROM {table_name}
                WHERE _rowid BETWEEN %s AND %s
                ORDER BY _rowid
            """

            with qdb.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (rowid_start, rowid_end))
                    rows = cur.fetchall()

            values = [r[0] for r in rows]
            pa_table = pa.Table.from_pydict({col: values})
            values_cb(pa_table)
        except Exception as e:
            failure_cb(e)


# =============================================================================
#  Wire it up
# =============================================================================

qdb_backend = QuestDBBackend(order_by_col=ORDER_BY_COL)

qdb_tds = TableDataService(
    backend=qdb_backend,
    page_size=PAGE_SIZE,   # controls DH paging / memory usage
)

qdb_trades_key = QuestDBTableKey(TARGET_TABLE_NAME)

# This Deephaven table is:
# - backed by QuestDB storage
# - streaming (refreshing=True)
# - WAL-driven (no count(*) polling)
trades = qdb_tds.make_table(qdb_trades_key, refreshing=True)


# Example usage in DH:
head_10 = trades.head(10)
