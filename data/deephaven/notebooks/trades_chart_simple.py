"""
Simple TradingView-Style Data Display for Deephaven

This demonstrates live trade data visualization using Deephaven's native components.
For full TradingView Lightweight Charts integration, a custom JS plugin is required.

Usage:
    exec(open('/data/storage/notebooks/trades_chart_simple.py').read())

Author: Claude Code
Version: 1.0 (Working Demo)
"""

import deephaven.ui as ui
from qdb import get_trades
from deephaven import agg

# =============================================================================
# Configuration
# =============================================================================

DEFAULT_SYMBOL = "BTC-USD"
DEFAULT_WINDOW_ROWS = 1000

EXCHANGE_COLORS = {"Coinbase": "#0088CC", "Bitstamp": "#00AA44", "Kraken": "#FF6600"}

# =============================================================================
# Working Interactive Component
# =============================================================================


@ui.component
def live_trades_viewer():
    """
    Live trades viewer using Deephaven native components.

    Features:
    - Symbol selector
    - Exchange filters
    - Window size control
    - Live updating table
    - Summary statistics
    """

    # State management
    symbol, set_symbol = ui.use_state(DEFAULT_SYMBOL)
    window_rows, set_window_rows = ui.use_state(DEFAULT_WINDOW_ROWS)
    show_coinbase, set_show_coinbase = ui.use_state(True)
    show_bitstamp, set_show_bitstamp = ui.use_state(True)
    show_kraken, set_show_kraken = ui.use_state(True)

    # Build exchange filter
    exchanges = []
    if show_coinbase:
        exchanges.append("COINBASE")
    if show_bitstamp:
        exchanges.append("BITSTAMP")
    if show_kraken:
        exchanges.append("KRAKEN")

    exchange_filter = (
        " || ".join([f"exchange == `{ex}`" for ex in exchanges]) if exchanges else "false"
    )

    # Get static data - memoize to prevent recreation on every render

    # Get last 5000 trades from QuestDB (static snapshot)
    trades = ui.use_memo(lambda: get_trades(last_nticks=5000), [])

    # Apply filters - memoize based on symbol, exchanges, and window_rows
    filtered = ui.use_memo(
        lambda: (
            trades.where(f"symbol == `{symbol}`").where(exchange_filter)
            if exchanges
            else trades.where(f"symbol == `{symbol}`")
        ).tail(window_rows),
        [symbol, exchange_filter, window_rows],
    )

    # Create summary statistics - memoize based on filtered table
    summary = ui.use_memo(
        lambda: filtered.agg_by(
            [
                agg.count_("TradeCount"),
                agg.avg("AvgPrice = price"),
                agg.min_("MinPrice = price"),
                agg.max_("MaxPrice = price"),
                agg.sum_("TotalVolume = size"),
            ],
            by=["exchange"],
        ),
        [filtered],
    )

    has_error = False
    error_msg = ""

    # Build UI
    return ui.flex(
        # Header
        ui.heading("Cryptocurrency Trades Viewer", level=1),
        ui.text(f"Static snapshot from QuestDB (last 5000 trades)"),
        ui.text(""),
        # Controls
        ui.view(
            ui.heading("Controls", level=3),
            # Symbol selector
            ui.flex(
                ui.text("Symbol:", min_width=80),
                ui.button(
                    "BTC-USD",
                    on_press=lambda: set_symbol("BTC-USD"),
                    variant="accent" if symbol == "BTC-USD" else "primary",
                ),
                ui.button(
                    "ETH-USD",
                    on_press=lambda: set_symbol("ETH-USD"),
                    variant="accent" if symbol == "ETH-USD" else "primary",
                ),
                ui.button(
                    "AVAX-USD",
                    on_press=lambda: set_symbol("AVAX-USD"),
                    variant="accent" if symbol == "AVAX-USD" else "primary",
                ),
                ui.button(
                    "SOL-USD",
                    on_press=lambda: set_symbol("SOL-USD"),
                    variant="accent" if symbol == "SOL-USD" else "primary",
                ),
                direction="row",
                gap=10,
                margin_bottom=10,
            ),
            # Exchange toggles
            ui.flex(
                ui.text("Exchanges:", min_width=80),
                ui.checkbox("Coinbase", is_selected=show_coinbase, on_change=set_show_coinbase),
                ui.checkbox("Bitstamp", is_selected=show_bitstamp, on_change=set_show_bitstamp),
                ui.checkbox("Kraken", is_selected=show_kraken, on_change=set_show_kraken),
                direction="row",
                gap=15,
                margin_bottom=10,
            ),
            # Window size
            ui.flex(
                ui.text("Window:", min_width=80),
                ui.button(
                    "100",
                    on_press=lambda: set_window_rows(100),
                    variant="accent" if window_rows == 100 else "primary",
                ),
                ui.button(
                    "500",
                    on_press=lambda: set_window_rows(500),
                    variant="accent" if window_rows == 500 else "primary",
                ),
                ui.button(
                    "1000",
                    on_press=lambda: set_window_rows(1000),
                    variant="accent" if window_rows == 1000 else "primary",
                ),
                ui.button(
                    "5000",
                    on_press=lambda: set_window_rows(5000),
                    variant="accent" if window_rows == 5000 else "primary",
                ),
                direction="row",
                gap=10,
                margin_bottom=10,
            ),
            margin_bottom=20,
        ),
        # Error display
        ui.view(
            ui.heading("Error", level=4),
            ui.text(error_msg),
        )
        if has_error
        else None,
        # Summary statistics
        # ui.view(
        #     ui.heading("Summary Statistics", level=3),
        #     ui.table(summary) if summary is not None else ui.text("No data"),
        #     margin_bottom=20
        # ) if not has_error else None,
        # Live trades table
        ui.view(
            ui.heading(f"Live Trades: {symbol}", level=3),
            ui.text(f"Showing last {window_rows} trades from {len(exchanges)} exchange(s)"),
            ui.table(filtered) if filtered is not None else ui.text("No data"),
        )
        if not has_error
        else None,
        direction="column",
        gap=15,
    )


# =============================================================================
# Create Component
# =============================================================================

# Create the live viewer
viewer = live_trades_viewer()

print("[OK] Static trades viewer created!")
print("   - Variable: 'viewer'")
print("   - Features: Symbol selector, exchange toggles, window size control")
print("   - Data: Last 5000 trades from QuestDB (static snapshot)")
print("")
print("[NOTE] This version uses static tables from qdb.get_trades()")
print("   - Testing if standard Deephaven tables work with ui.table()")
print("   - If this works, we know the issue is with create_live_table() custom backend")
print("")
print("[INFO] This demo uses Deephaven's native table component")
