"""
QuestDB Backend for Deephaven TableDataService

This module provides a live streaming backend that connects QuestDB tables
to Deephaven via the TableDataService API.

Features:
- Singleton pattern: one backend instance per table
- Live streaming with automatic size monitoring
- Thread-safe with proper cleanup
- Re-runnable without restart

Usage:
    from qdb_backend import QuestDBBackend, QuestDBTableKey, create_live_table
    
    # Create a live table
    trades = create_live_table('trades')
    
    # Stop monitoring
    from qdb_backend import stop_monitoring
    stop_monitoring()
"""

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

DEFAULT_ORDER_BY_COL = "timestamp"
DEFAULT_PAGE_SIZE = 64_000
WAL_IDLE_SLEEP_SEC = 0.05  # Polling interval for size changes


# =============================================================================
#  Global Registry (Singleton per table)
# =============================================================================

# Global registry to track backends BY TABLE NAME (singleton per table)
# This MUST be at module level to persist when class is redefined
if '_GLOBAL_BACKENDS_BY_TABLE' not in globals():
    _GLOBAL_BACKENDS_BY_TABLE = {}  # table_name -> backend instance
    _GLOBAL_BACKEND_LOCK = threading.Lock()


# =============================================================================
#  TableKey & TableLocationKey
# =============================================================================

class QuestDBTableKey(TableKey):
    """TableKey implementation for QuestDB tables"""
    
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
#  Backend Implementation
# =============================================================================

class QuestDBBackend(TableDataServiceBackend, ABC):
    """
    QuestDB-backed TableDataServiceBackend with live streaming.

    Features:
    - One QuestDB table -> one Deephaven table key
    - Single location (no partitioning yet)
    - Append-only, ordered by order_by_col (e.g., timestamp)
    - Polls count(*) to detect new rows
    - Uses ROW_NUMBER() for paging (QuestDB doesn't support OFFSET)
    - Smart thread management: stops old threads before starting new ones
    """

    def __init__(self, order_by_col: str = DEFAULT_ORDER_BY_COL, table_name: str = None, verbose: bool = False):
        super().__init__()
        self._order_by_col = order_by_col
        self._table_name = table_name
        self._verbose = verbose
        self._active_threads = {}
        self._lock = threading.Lock()
        
        print(f"[Backend] Created new QuestDBBackend instance (id={id(self)}, table='{table_name}')")
    
    def __del__(self):
        """Cleanup when backend is destroyed"""
        self.cleanup()
    
    def set_verbose(self, verbose: bool):
        """Enable or disable verbose logging"""
        self._verbose = verbose
        print(f"[Backend] Verbose logging {'enabled' if verbose else 'disabled'} for table '{self._table_name}'")
    
    def cleanup(self):
        """Stop all active threads for this backend"""
        threads_to_stop = []
        with self._lock:
            if self._active_threads:
                print(f"[Backend] Cleaning up {len(self._active_threads)} active threads...")
                for table_name, (stop_event, thread) in list(self._active_threads.items()):
                    stop_event.set()
                    threads_to_stop.append((table_name, thread))
                    print(f"[Backend] Signaled thread for '{table_name}' to stop")
                self._active_threads.clear()
        
        # Wait for threads to stop (outside lock to avoid deadlock)
        for table_name, thread in threads_to_stop:
            thread.join(timeout=2.0)
            if thread.is_alive():
                print(f"[Backend] Warning: Thread for '{table_name}' didn't stop within 2 seconds")
            else:
                print(f"[Backend] Thread for '{table_name}' stopped cleanly")
        
        # Unregister this instance from GLOBAL registry
        if self._table_name:
            with _GLOBAL_BACKEND_LOCK:
                if self._table_name in _GLOBAL_BACKENDS_BY_TABLE:
                    del _GLOBAL_BACKENDS_BY_TABLE[self._table_name]
                    print(f"[Backend] Unregistered backend for table '{self._table_name}'")
    
    @classmethod
    def get_or_create(cls, table_name: str, order_by_col: str = DEFAULT_ORDER_BY_COL, verbose: bool = False):
        """Get existing backend for table or create new one (singleton pattern)"""
        with _GLOBAL_BACKEND_LOCK:
            if table_name in _GLOBAL_BACKENDS_BY_TABLE:
                existing = _GLOBAL_BACKENDS_BY_TABLE[table_name]
                print(f"[Backend] Reusing existing backend for table '{table_name}' (id={id(existing)})")
                return existing
            else:
                print(f"[Backend] Creating new backend for table '{table_name}'...")
                new_backend = cls(order_by_col=order_by_col, table_name=table_name, verbose=verbose)
                _GLOBAL_BACKENDS_BY_TABLE[table_name] = new_backend
                return new_backend
    
    @classmethod
    def cleanup_all(cls):
        """Stop all threads across all backend instances"""
        with _GLOBAL_BACKEND_LOCK:
            if _GLOBAL_BACKENDS_BY_TABLE:
                print(f"[Backend] Cleaning up all backends ({len(_GLOBAL_BACKENDS_BY_TABLE)} tables)...")
                # Stop threads first (outside the lock in cleanup())
                backends_to_cleanup = list(_GLOBAL_BACKENDS_BY_TABLE.values())
                _GLOBAL_BACKENDS_BY_TABLE.clear()  # Clear registry immediately
                
        # Now cleanup outside the lock
        for instance in backends_to_cleanup:
            # Stop threads but don't try to unregister (already cleared)
            with instance._lock:
                if instance._active_threads:
                    print(f"[Backend] Cleaning up {len(instance._active_threads)} active threads for '{instance._table_name}'...")
                    threads_to_stop = []
                    for table_name, (stop_event, thread) in list(instance._active_threads.items()):
                        stop_event.set()
                        threads_to_stop.append((table_name, thread))
                        print(f"[Backend] Signaled thread for '{table_name}' to stop")
                    instance._active_threads.clear()
                    
                    # Wait for threads outside lock
                    for table_name, thread in threads_to_stop:
                        thread.join(timeout=2.0)
                        if thread.is_alive():
                            print(f"[Backend] Warning: Thread for '{table_name}' didn't stop within 2 seconds")
                        else:
                            print(f"[Backend] Thread for '{table_name}' stopped cleanly")
        
        if not _GLOBAL_BACKENDS_BY_TABLE:
            print(f"[Backend] All backends cleaned up successfully")

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
            if t in ("TIMESTAMP", "TIMESTAMPTZ", "TIMESTAMP WITHOUT TIME ZONE"):
                arrow_type = pa.timestamp("us", tz="UTC")
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
        """
        For refreshing tables, this should notify about new locations.
        Even with a single location, we might need to re-notify when data changes.
        
        NOTE: Investigating if this is why column_values isn't being called!
        """
        try:
            # Send initial location
            location_cb(SINGLE_LOCATION_KEY, None)
            success_cb()
            
            if self._verbose:
                print(f"[Backend] subscribe_to_table_locations called for '{table_key.table_name}'")

            def unsubscribe():
                if self._verbose:
                    print(f"[Backend] Unsubscribed from table locations")
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

    def _get_table_size(self, table_name: str):
        """
        Get current row count for this table using WAL transactions.
        This is more efficient than count(*) for WAL-enabled tables.
        """
        with qdb.get_connection() as conn:
            with conn.cursor() as cur:
                # Try to use wal_transactions() for WAL-enabled tables
                try:
                    cur.execute(f"""
                        SELECT SUM(rowCount) 
                        FROM wal_transactions('{table_name}')
                    """)
                    result = cur.fetchone()
                    if result and result[0] is not None:
                        return int(result[0])
                except Exception:
                    # Fall back to count(*) if wal_transactions() not available
                    pass
                
                # Fallback to count(*)
                cur.execute(f"SELECT count(*) FROM {table_name}")
                (count,) = cur.fetchone()
                return int(count or 0)

    def _wal_watch_loop(self, table_key, size_cb, success_cb, failure_cb, stop_event):
        """
        Background loop using wal_transactions() for efficient change detection.
        
        This uses QuestDB's wal_transactions() function which tracks:
        - sequencerTxn: Transaction ID
        - minTimestamp/maxTimestamp: Range of data in transaction
        - rowCount: Number of rows in transaction
        - structureVersion: Schema version
        
        This is more efficient than count(*) polling as it only queries metadata.
        """
        try:
            assert isinstance(table_key, QuestDBTableKey)
            table_name = table_key.table_name

            # Initial: get table size and latest transaction
            last_size = 0
            last_txn_id = 0
            use_wal_tracking = False
            
            with qdb.get_connection() as conn:
                with conn.cursor() as cur:
                    # Try to use wal_transactions() for WAL-enabled tables
                    try:
                        # Get latest transaction ID and rowCount sum
                        cur.execute(f"""
                            SELECT sequencerTxn, SUM(rowCount) OVER ()
                            FROM wal_transactions('{table_name}')
                            ORDER BY sequencerTxn DESC
                            LIMIT 1
                        """)
                        result = cur.fetchone()
                        if result:
                            last_txn_id = result[0]
                            wal_row_sum = result[1]
                            
                            # Use rowCount sum if available, otherwise get actual count
                            if wal_row_sum is not None and wal_row_sum > 0:
                                last_size = int(wal_row_sum)
                                print(f"[WAL Backend:{table_name}] Using wal_transactions() with rowCount")
                            else:
                                # rowCount is NULL - we MUST get actual table size for viewport calculations
                                # But we'll still use txn IDs to detect changes
                                cur.execute(f"SELECT count(*) FROM {table_name}")
                                last_size = int(cur.fetchone()[0])
                                print(f"[WAL Backend:{table_name}] rowCount NULL, got actual size via count(*): {last_size:,} rows")
                            
                            use_wal_tracking = True
                    except Exception as e:
                        # Fall back to count(*) if wal_transactions() not available
                        print(f"[WAL Backend:{table_name}] wal_transactions() not available, using count(*) polling: {e}")
                        last_size = self._get_table_size(table_name)
            
            size_cb(last_size)
            success_cb()

            print(f"[WAL Backend:{table_name}] Starting monitoring, initial size: {last_size:,}, txn: {last_txn_id}")

            while not stop_event.is_set():
                time.sleep(WAL_IDLE_SLEEP_SEC)
                
                if use_wal_tracking:
                    # Efficient WAL transaction tracking - no count(*) needed!
                    with qdb.get_connection() as conn:
                        with conn.cursor() as cur:
                            cur.execute(f"""
                                SELECT sequencerTxn, rowCount, minTimestamp, maxTimestamp
                                FROM wal_transactions('{table_name}')
                                WHERE sequencerTxn > {last_txn_id}
                                ORDER BY sequencerTxn
                            """)
                            new_transactions = cur.fetchall()
                            
                            if new_transactions:
                                # Update to latest transaction
                                last_txn_id = new_transactions[-1][0]
                                
                                # Check if rowCount is available
                                row_counts = [txn[1] for txn in new_transactions if txn[1] is not None]
                                
                                if row_counts:
                                    # Sum up new rows from WAL
                                    new_rows = sum(row_counts)
                                    current_size = last_size + new_rows
                                    
                                    if self._verbose:
                                        print(f"[WAL Backend:{table_name}] New transactions: {len(new_transactions)}, "
                                              f"size: {last_size:,} -> {current_size:,} (+{new_rows:,}), "
                                              f"txn: {last_txn_id}")
                                else:
                                    # rowCount NULL - we detected a change via txn ID, now get actual new size
                                    # This is still efficient - we only query when we KNOW data changed
                                    cur.execute(f"SELECT count(*) FROM {table_name}")
                                    current_size = int(cur.fetchone()[0])
                                    
                                    if self._verbose:
                                        print(f"[WAL Backend:{table_name}] New transactions: {len(new_transactions)}, "
                                              f"size: {last_size:,} -> {current_size:,} (+{current_size - last_size}), "
                                              f"txn: {last_txn_id}")
                                
                                last_size = current_size
                                
                                # Call size_cb to notify Deephaven
                                if self._verbose:
                                    import datetime
                                    ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
                                    print(f"[WAL Backend:{table_name}] [{ts}] Calling size_cb({current_size})")
                                
                                size_cb(current_size)
                                
                                if self._verbose:
                                    ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
                                    print(f"[WAL Backend:{table_name}] [{ts}] size_cb returned")
                else:
                    # Fallback to count(*) polling
                    current_size = self._get_table_size(table_name)
                    if current_size > last_size:
                        print(f"[WAL Backend:{table_name}] Size changed: {last_size:,} -> {current_size:,} (+{current_size - last_size})")
                        last_size = current_size
                        size_cb(current_size)

        except Exception as e:
            failure_cb(e)

    def table_location_size(self, table_key, table_location_key, size_cb, failure_cb):
        """
        Static size (non-refreshing use).
        """
        try:
            assert isinstance(table_key, QuestDBTableKey)
            table_name = table_key.table_name
            size = self._get_table_size(table_name)
            size_cb(size)
        except Exception as e:
            failure_cb(e)

    def subscribe_to_table_location_size(
        self, table_key, table_location_key, size_cb, success_cb, failure_cb
    ):
        """
        Refreshing size – WAL-driven, not interval count(*) polling.
        """
        table_name = table_key.table_name
        
        # Check if we already have a thread for this table (with lock)
        old_thread = None
        with self._lock:
            if table_name in self._active_threads:
                print(f"[WAL Backend] Thread already exists for '{table_name}', stopping old one...")
                old_stop, old_thread = self._active_threads[table_name]
                old_stop.set()
        
        # Wait for old thread to stop (outside lock to avoid deadlock)
        if old_thread:
            old_thread.join(timeout=1.0)
            if old_thread.is_alive():
                print(f"[WAL Backend] Warning: Old thread for '{table_name}' didn't stop cleanly")
        
        # Create and start new thread
        stop_event = threading.Event()
        
        t = threading.Thread(
            target=self._wal_watch_loop,
            args=(table_key, size_cb, success_cb, failure_cb, stop_event),
            daemon=True,
            name=f"QuestDB-Monitor-{table_name}-{id(self)}"
        )
        t.start()
        
        # Track this thread (with lock)
        with self._lock:
            self._active_threads[table_name] = (stop_event, t)
        
        print(f"[WAL Backend] Started new monitoring thread for '{table_name}' (backend id={id(self)})")

        def unsubscribe():
            print(f"[WAL Backend] Unsubscribe called for '{table_name}'")
            with self._lock:
                stop_event.set()
                if table_name in self._active_threads:
                    del self._active_threads[table_name]

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
        using ROW_NUMBER() instead of LIMIT/OFFSET (QuestDB compatibility).
        Deephaven's offset is 0-based; we assume _rowid is 1-based.
        """
        try:
            assert isinstance(table_key, QuestDBTableKey)
            table_name = table_key.table_name

            # Timing for debugging
            if self._verbose:
                import datetime
                start_time = datetime.datetime.now()
                ts = start_time.strftime("%H:%M:%S.%f")[:-3]
                print(f"[Backend:{table_name}] [{ts}] column_values: col={col}, offset={offset}, max_rows={max_rows}")

            # Use ROW_NUMBER() since QuestDB doesn't support OFFSET
            sql = f"""
                WITH numbered AS (
                    SELECT {col}, ROW_NUMBER() OVER (ORDER BY {self._order_by_col}) as rn
                    FROM {table_name}
                )
                SELECT {col}
                FROM numbered
                WHERE rn > %s AND rn <= %s
            """

            with qdb.get_connection() as conn:
                with conn.cursor() as cur:
                    # QuestDB ROW_NUMBER() is 1-based
                    start_rn = offset
                    end_rn = offset + max_rows
                    
                    if self._verbose:
                        query_start = datetime.datetime.now()
                    
                    cur.execute(sql, (start_rn, end_rn))
                    rows = cur.fetchall()
                    
                    if self._verbose:
                        query_end = datetime.datetime.now()
                        query_ms = (query_end - query_start).total_seconds() * 1000
                        print(f"[Backend:{table_name}] Query took {query_ms:.1f}ms, returned {len(rows)} rows")

            values = [r[0] for r in rows]
            
            # Convert to PyArrow with proper types
            # Get the column type from schema
            schema = None
            for field in self._get_schema_from_information_schema(table_name).names:
                if field == col:
                    schema = self._get_schema_from_information_schema(table_name)
                    break
            
            if schema and col in schema.names:
                field_type = schema.field(col).type
                
                if pa.types.is_timestamp(field_type):
                    import datetime
                    # Ensure timezone aware
                    values = [v.replace(tzinfo=datetime.timezone.utc) if v and isinstance(v, datetime.datetime) and v.tzinfo is None else v for v in values]
                    arrow_array = pa.array(values, type=field_type)
                    pa_table = pa.table({col: arrow_array})
                elif pa.types.is_floating(field_type):
                    values = [float(v) if v is not None else None for v in values]
                    arrow_array = pa.array(values, type=field_type)
                    pa_table = pa.table({col: arrow_array})
                elif pa.types.is_string(field_type):
                    values = [str(v) if v is not None else None for v in values]
                    arrow_array = pa.array(values, type=field_type)
                    pa_table = pa.table({col: arrow_array})
                else:
                    pa_table = pa.Table.from_pydict({col: values})
            else:
                pa_table = pa.Table.from_pydict({col: values})
            
            values_cb(pa_table)
        except Exception as e:
            failure_cb(e)


# =============================================================================
#  Convenience Functions
# =============================================================================

def create_live_table(table_name: str, order_by_col: str = DEFAULT_ORDER_BY_COL, 
                     page_size: int = DEFAULT_PAGE_SIZE, refreshing: bool = True, verbose: bool = False):
    """
    Create a live Deephaven table backed by QuestDB.
    
    Args:
        table_name: Name of the QuestDB table
        order_by_col: Column to order by (default: "timestamp")
        page_size: Page size for Deephaven (default: 64,000)
        refreshing: Whether to enable live updates (default: True)
        verbose: Enable verbose logging of transaction updates (default: False)
    
    Returns:
        Deephaven table object
    
    Example:
        trades = create_live_table('trades')
        trades = create_live_table('trades', verbose=True)  # With detailed logs
    """
    backend = QuestDBBackend.get_or_create(
        table_name=table_name,
        order_by_col=order_by_col,
        verbose=verbose
    )
    
    tds = TableDataService(backend=backend, page_size=page_size)
    table_key = QuestDBTableKey(table_name)
    
    return tds.make_table(table_key, refreshing=refreshing)


def set_verbose(table_name: str, verbose: bool):
    """
    Enable or disable verbose logging for a specific table's backend.
    
    Args:
        table_name: Name of the table
        verbose: True to enable verbose logging, False to disable
    
    Example:
        set_verbose('trades', True)   # Enable verbose logging
        set_verbose('trades', False)  # Disable verbose logging
    """
    with _GLOBAL_BACKEND_LOCK:
        if table_name in _GLOBAL_BACKENDS_BY_TABLE:
            backend = _GLOBAL_BACKENDS_BY_TABLE[table_name]
            backend.set_verbose(verbose)
        else:
            print(f"[Backend] No backend found for table '{table_name}'")


def stop_monitoring():
    """
    Stop all QuestDB monitoring threads.
    
    This will cleanly stop all background threads that are monitoring
    QuestDB tables for changes. After calling this, logs will stop ticking.
    
    To restart monitoring, simply create a new table with create_live_table()
    or re-run your script.
    
    Example:
        stop_monitoring()
    """
    QuestDBBackend.cleanup_all()
    print("✅ All monitoring stopped! Logs will stop ticking.")
    print("   Run create_live_table() again to restart monitoring.")
