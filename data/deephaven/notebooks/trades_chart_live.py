"""
Live trades chart for Deephaven

Displays real-time tick trades from QuestDB as a native Deephaven line plot,
one line per exchange (Coinbase, Bitstamp, Kraken), with interactive controls.

deephaven.ui cannot embed custom JavaScript (there is no html.script / html.iframe),
so this uses deephaven.plot.express, which updates live as new trades arrive.

Usage:
    exec(open('/data/storage/notebooks/trades_chart_live.py').read())

Widgets created:
    - chart_interactive: chart with symbol / exchange / window-size controls
"""

import deephaven.plot.express as dx
import deephaven.ui as ui
from qdb_backend import create_live_table

# =============================================================================
# Configuration
# =============================================================================

DEFAULT_SYMBOL = "BTC-USD"
DEFAULT_WINDOW_ROWS = 1000
SYMBOLS = ["BTC-USD", "ETH-USD", "AVAX-USD", "SOL-USD"]
WINDOW_SIZES = [100, 500, 1000, 5000]

# Exchange names are upper case in QuestDB
EXCHANGE_COLORS = {
    "COINBASE": "#0088CC",  # Blue
    "BITSTAMP": "#00AA44",  # Green
    "KRAKEN": "#FF6600",  # Orange
}

# Live QuestDB-backed table (the backend registry makes re-running this file safe)
trades_live = create_live_table("trades", refreshing=True)


# =============================================================================
# Components
# =============================================================================


@ui.component
def tick_chart(symbol=DEFAULT_SYMBOL, window_rows=DEFAULT_WINDOW_ROWS, exchanges=None):
    """Line plot of the last `window_rows` trades of `symbol`, one line per exchange."""
    exchanges = tuple(exchanges) if exchanges else tuple(EXCHANGE_COLORS)

    def make_figure():
        names = ", ".join(f"`{ex}`" for ex in exchanges)
        recent = trades_live.where([f"symbol = `{symbol}`", f"exchange in {names}"]).tail(
            window_rows
        )
        return dx.line(
            recent,
            x="timestamp",
            y="price",
            by="exchange",
            color_discrete_map=EXCHANGE_COLORS,
            title=f"Live trades: {symbol} (last {window_rows})",
        )

    return ui.use_memo(make_figure, [symbol, window_rows, exchanges])


@ui.component
def tick_chart_interactive():
    """Chart with symbol, exchange and window-size controls."""
    symbol, set_symbol = ui.use_state(DEFAULT_SYMBOL)
    window_rows, set_window_rows = ui.use_state(DEFAULT_WINDOW_ROWS)
    show_coinbase, set_show_coinbase = ui.use_state(True)
    show_bitstamp, set_show_bitstamp = ui.use_state(True)
    show_kraken, set_show_kraken = ui.use_state(True)

    exchanges = []
    if show_coinbase:
        exchanges.append("COINBASE")
    if show_bitstamp:
        exchanges.append("BITSTAMP")
    if show_kraken:
        exchanges.append("KRAKEN")

    symbol_buttons = [
        ui.button(
            s,
            key=s,
            on_press=lambda _event, s=s: set_symbol(s),
            variant="accent" if symbol == s else "secondary",
        )
        for s in SYMBOLS
    ]
    window_buttons = [
        ui.button(
            str(n),
            key=str(n),
            on_press=lambda _event, n=n: set_window_rows(n),
            variant="accent" if window_rows == n else "secondary",
        )
        for n in WINDOW_SIZES
    ]

    return ui.flex(
        ui.flex(
            ui.button_group(*symbol_buttons),
            ui.button_group(*window_buttons),
            ui.checkbox("Coinbase", is_selected=show_coinbase, on_change=set_show_coinbase),
            ui.checkbox("Bitstamp", is_selected=show_bitstamp, on_change=set_show_bitstamp),
            ui.checkbox("Kraken", is_selected=show_kraken, on_change=set_show_kraken),
            gap="size-200",
            wrap=True,
            align_items="center",
        ),
        tick_chart(symbol=symbol, window_rows=window_rows, exchanges=exchanges)
        if exchanges
        else ui.text("Select at least one exchange"),
        direction="column",
        flex_grow=1,
    )


# =============================================================================
# Create widgets
# =============================================================================

chart_interactive = tick_chart_interactive()
chart = tick_chart(symbol=DEFAULT_SYMBOL)

print("[OK] 'chart_interactive' (controls + chart) and 'chart' (BTC-USD, all exchanges) created")
