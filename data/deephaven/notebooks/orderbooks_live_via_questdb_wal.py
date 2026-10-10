# Live Orderbooks from QuestDB - TableDataService over QuestDB storage
#
# This creates a Deephaven table backed by QuestDB storage that ticks as new rows settle
# (qdb_backend polls QuestDB every 50 ms). bids/asks arrive as double[][] over QWP.
#
# REQUIREMENTS:
# 1. Deephaven 42.6 or newer (TableDataService callbacks fixed upstream)
# 2. QuestDB with orderbooks table receiving data
#
# Usage:
#   Simply run this file, it will create an 'orderbooks' table that auto-updates
#   Or use: from qdb_backend import create_live_table

from qdb_backend import create_live_table, stop_monitoring

# =============================================================================
#  Configuration – EDIT THESE FOR YOUR ENV
# =============================================================================

TARGET_TABLE_NAME = "orderbooks"  # QuestDB table to stream
ORDER_BY_COL = "timestamp"  # Order column (usually timestamp)
PAGE_SIZE = 32_000  # Deephaven page size (smaller for orderbooks)
# "qwp" (default): Arrow pages over QuestDB's WebSocket protocol (see qdb_qwp.py); "pg":
# Postgres wire (arrays as strings). Set TRANSPORT before running to override.
TRANSPORT = globals().get("TRANSPORT", "qwp")
# Rows appear once settled, SETTLE_SECONDS after their timestamp, so every row is exact and
# row positions never move (see qdb_backend.py). latency_seconds below shows that delay.
SETTLE_SECONDS = globals().get("SETTLE_SECONDS", 3.0)
# orderbooks has TTL 1 HOUR: QuestDB drops whole hour partitions, after which rows Deephaven
# re-reads from them come back null. Start 15 min back (None: the whole table).
HISTORY_SECONDS = globals().get("HISTORY_SECONDS", 900)


# =============================================================================
#  Create Live Table
# =============================================================================
orderbooks = create_live_table(
    table_name=TARGET_TABLE_NAME,
    order_by_col=ORDER_BY_COL,
    page_size=PAGE_SIZE,
    refreshing=True,
    use_liveness_scope=True,
    transport=TRANSPORT,
    settle_seconds=SETTLE_SECONDS,
    history_seconds=HISTORY_SECONDS,
).sort_descending(order_by=["timestamp"])

# bids / asks arrive as double[][] ([[prices], [sizes]]). The grid shows a nested array as
# "[[D@3343baa0,[D@3e14f421]" (Java's default toString), so split each into two double[]
# columns, the same shape qdb.get_orderbooks() returns, and add best bid/ask and latency.
orderbooks = orderbooks.view(
    [
        "timestamp",
        "exchange",
        "symbol",
        "best_bid = bids[0][0]",
        "best_ask = asks[0][0]",
        "bid_prices = bids[0]",
        "bid_sizes = bids[1]",
        "ask_prices = asks[0]",
        "ask_sizes = asks[1]",
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
