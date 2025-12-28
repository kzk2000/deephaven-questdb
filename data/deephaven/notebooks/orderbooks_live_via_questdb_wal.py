# Live Orderbooks from QuestDB - Real-time Updates via WAL Monitoring
#
# This creates a Deephaven table backed by QuestDB storage with real-time updates
# by monitoring QuestDB's WAL (Write-Ahead Log) for new rows.
#
# REQUIREMENTS:
# 1. Patched Deephaven (run patch_all_callbacks.py first)
# 2. QuestDB with orderbooks table receiving data
#
# Usage:
#   Simply run this file, it will create an 'orderbooks' table that auto-updates
#   Or use: from qdb_backend import create_live_table

from qdb_backend import create_live_table, stop_monitoring
from deephaven.liveness_scope import LivenessScope

# =============================================================================
#  Configuration – EDIT THESE FOR YOUR ENV
# =============================================================================

TARGET_TABLE_NAME = "orderbooks"  # QuestDB table to stream
ORDER_BY_COL = "timestamp"  # Order column (usually timestamp)
PAGE_SIZE = 32_000  # Deephaven page size (smaller for orderbooks)


# =============================================================================
#  Create Live Table
# =============================================================================

# Create live table using the convenience function with proper LivenessScope management
scope = LivenessScope()
with scope.open():
    orderbooks = create_live_table(
        table_name=TARGET_TABLE_NAME,
        order_by_col=ORDER_BY_COL,
        page_size=PAGE_SIZE,
        refreshing=True,
    ).sort_descending(order_by=["timestamp"])

# Add latency calculations
orderbooks = orderbooks.update_view(
    [
        "latency_seconds = diffNanos(timestamp, now()) / 1e9",
    ]
)

if False:
    # Example: Toggle verbose mode
    from qdb_backend import set_verbose

    set_verbose("orderbooks", True)  # Watch the logs!
    # ... wait a few seconds to see transactions ...
    set_verbose("orderbooks", False)  # Quiet again


if False:
    # Stop all tables from ticking
    stop_monitoring()
