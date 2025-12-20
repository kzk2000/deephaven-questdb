
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
from deephaven.liveness_scope import LivenessScope

# =============================================================================
#  Configuration – EDIT THESE FOR YOUR ENV
# =============================================================================

TARGET_TABLE_NAME = "trades"  # QuestDB table to stream
ORDER_BY_COL = "timestamp"    # Order column (usually timestamp)
PAGE_SIZE = 64_000            # Deephaven page size


# =============================================================================
#  Create Live Table
# =============================================================================

# Create live table using the convenience function with proper LivenessScope management
scope = LivenessScope()
with scope.open():
    trades = create_live_table(
        table_name=TARGET_TABLE_NAME,
        order_by_col=ORDER_BY_COL,
        page_size=PAGE_SIZE,
        refreshing=True
    ).sort_descending(order_by=['timestamp'])

trades = trades.update_view([
    'latency_seconds = diffNanos(timestamp, now()) / 1e9',
])


if False:
    from qdb_backend import set_verbose
    set_verbose('trades', True)   # Watch the logs!
    # ... wait a few seconds to see transactions ...
    set_verbose('trades', False)  # Quiet again


if False:
    # stop trades table from ticking
    stop_monitoring()
