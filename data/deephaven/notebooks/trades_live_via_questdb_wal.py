
# FIXED VERSION - Works with patched TableDataService API
#
# This creates a Deephaven table backed by QuestDB storage with real-time updates
# by monitoring QuestDB for new rows.
#
# REQUIREMENTS: 
# 1. Patched Deephaven (run patch_all_callbacks.py first)
# 2. QuestDB with data in a table
#
# Usage:
#   Simply run this file, it will create a 'trades' table
#   Or use: from qdb_backend import create_live_table

from qdb_backend import create_live_table, stop_monitoring

# =============================================================================
#  Configuration – EDIT THESE FOR YOUR ENV
# =============================================================================

TARGET_TABLE_NAME = "trades"  # QuestDB table to stream
ORDER_BY_COL = "timestamp"    # Order column (usually timestamp)
PAGE_SIZE = 64_000            # Deephaven page size


# =============================================================================
#  Create Live Table
# =============================================================================

# Create live table using the convenience function
trades = create_live_table(
    table_name=TARGET_TABLE_NAME,
    order_by_col=ORDER_BY_COL,
    page_size=PAGE_SIZE,
    refreshing=True
).sort_descending(order_by=['timestamp'])

trades = trades.update_view([
    'now_value = now()',
    'diff_nanos = diffNanos(timestamp, now())',
    'diff_seconds = diff_nanos / 1e9',
])

kk= trades.head(10)


# Print success message
print("="*70)
print("SUCCESS - Live trades table created!")
print("="*70)
print(f"Table: trades")
print(f"Size: {trades.size:,} rows")
print(f"Columns: {', '.join([col.name for col in trades.columns])}")
print("\nUsage examples:")
print("  - View first 10: trades.head(10)")
print("  - Filter by symbol: trades.where('symbol == \"BTC-USD\"')")
print("  - Latest trades: trades.tail(20)")
print("  - Check size: trades.size")
print("\nTo stop monitoring:")
print("  - stop_monitoring()  # Stops all monitoring threads")
print("="*70)

from qdb_backend import set_verbose
set_verbose('trades', True)   # Watch the logs!


if False:
    from qdb_backend import set_verbose
    set_verbose('trades', True)   # Watch the logs!
    # ... wait a few seconds to see transactions ...
    set_verbose('trades', False)  # Quiet again


if False:
    # stop trades table from ticking
    stop_monitoring()
