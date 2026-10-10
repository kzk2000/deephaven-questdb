"""
Live TradingView Lightweight Charts for Deephaven

Real-time trades from QuestDB, drawn with the official
deephaven-plugin-tradingview-lightweight plugin (TradingView Lightweight Charts).

Two views, switchable in the UI:
    - Candles: OHLC candlesticks binned in the engine from raw ticks, with a
      volume histogram in a second pane
    - Ticks:   raw trade prices, one line per exchange

Usage:
    exec(open('/data/storage/notebooks/trades_chart_live.py').read())

Widgets created:
    - chart_interactive: chart with symbol / view / bin / window / exchange controls
    - chart_candles:     BTC-USD 5s candles with volume (fixed layout, live data)
    - chart_ticks:       BTC-USD tick lines per exchange (fixed layout, live data)
"""

import deephaven.plot.tradingview_lightweight as tvl
import deephaven.ui as ui
from deephaven import agg
from qdb_backend import create_live_table

# =============================================================================
# Configuration
# =============================================================================

DEFAULT_SYMBOL = "BTC-USD"
DEFAULT_WINDOW_ROWS = 1000
DEFAULT_BIN_SECONDS = 5
SYMBOLS = ["BTC-USD", "ETH-USD", "AVAX-USD", "SOL-USD"]
WINDOW_SIZES = [500, 1000, 5000]
BIN_SECONDS = {"1s": 1, "5s": 5, "30s": 30, "1m": 60}
EXCHANGES = ["COINBASE", "BITSTAMP", "KRAKEN"]  # upper case in QuestDB

# Live QuestDB-backed table (the backend registry makes re-running this file safe)
trades_live = create_live_table("trades", refreshing=True)


# =============================================================================
# Chart builders
# =============================================================================


def recent_trades(symbol, window_rows, exchanges):
    """Last `window_rows` trades for `symbol` on the given exchanges (live)."""
    names = ", ".join(f"`{ex}`" for ex in exchanges)
    return trades_live.where([f"symbol = `{symbol}`", f"exchange in {names}"]).tail(window_rows)


def candle_chart(
    symbol=DEFAULT_SYMBOL,
    window_rows=DEFAULT_WINDOW_ROWS,
    bin_seconds=DEFAULT_BIN_SECONDS,
    exchanges=tuple(EXCHANGES),
):
    """OHLC candles binned from ticks in the engine, with a volume histogram in pane 1."""
    ticks = recent_trades(symbol, window_rows, exchanges)
    # Reduce ticks to one OHLCV row per time bin (ticks are time-ordered)
    ohlc = (
        ticks.update_view([f"Bin = lowerBin(timestamp, {bin_seconds} * SECOND)"])
        .agg_by(
            [
                agg.first("Open = price"),
                agg.max_("High = price"),
                agg.min_("Low = price"),
                agg.last("Close = price"),
                agg.sum_("Volume = size"),
            ],
            by=["Bin"],
        )
        .rename_columns(["timestamp = Bin"])
    )
    candles = tvl.candlestick(
        ohlc,
        timestamp="timestamp",
        open="Open",
        high="High",
        low="Low",
        close="Close",
        title=symbol,
    )
    volume = tvl.histogram(
        ohlc,
        timestamp="timestamp",
        value="Volume",
        color="rgba(96,165,250,0.5)",
        pane=1,
        title="Volume",
    )
    return tvl.chart(
        candles,
        volume,
        pane_stretch_factors=[3, 1],
        time_scale=tvl.time_scale(time_visible=True, seconds_visible=True),
    )


def tick_chart(
    symbol=DEFAULT_SYMBOL,
    window_rows=DEFAULT_WINDOW_ROWS,
    exchanges=tuple(EXCHANGES),
):
    """Raw trade prices, one line per exchange."""
    ticks = recent_trades(symbol, window_rows, exchanges)
    return tvl.chart(
        tvl.line(ticks, timestamp="timestamp", value="price", by="exchange"),
        time_scale=tvl.time_scale(time_visible=True, seconds_visible=True),
    )


# =============================================================================
# Interactive component
# =============================================================================


@ui.component
def tick_chart_interactive():
    """Chart with view, symbol, bin width, window size and exchange controls."""
    view, set_view = ui.use_state("candles")
    symbol, set_symbol = ui.use_state(DEFAULT_SYMBOL)
    window_rows, set_window_rows = ui.use_state(DEFAULT_WINDOW_ROWS)
    bin_seconds, set_bin_seconds = ui.use_state(DEFAULT_BIN_SECONDS)
    show_coinbase, set_show_coinbase = ui.use_state(True)
    show_bitstamp, set_show_bitstamp = ui.use_state(True)
    show_kraken, set_show_kraken = ui.use_state(True)

    exchanges = tuple(
        ex for ex, shown in zip(EXCHANGES, [show_coinbase, show_bitstamp, show_kraken]) if shown
    )

    # Rebuilt only when a control changes, so ticking data does not recreate the chart
    def make_chart():
        if not exchanges:
            return None
        if view == "candles":
            return candle_chart(symbol, window_rows, bin_seconds, exchanges)
        return tick_chart(symbol, window_rows, exchanges)

    chart = ui.use_memo(make_chart, [view, symbol, window_rows, bin_seconds, exchanges])

    def choice(label, value, current, setter):
        # on_press passes an event object first, so the handler must accept and ignore it
        return ui.button(
            label,
            key=f"{label}-{value}",
            on_press=lambda _event, v=value: setter(v),
            variant="accent" if current == value else "secondary",
        )

    controls = [
        ui.button_group(
            choice("Candles", "candles", view, set_view),
            choice("Ticks", "ticks", view, set_view),
        ),
        ui.button_group(*[choice(s, s, symbol, set_symbol) for s in SYMBOLS]),
        ui.button_group(*[choice(str(n), n, window_rows, set_window_rows) for n in WINDOW_SIZES]),
    ]
    if view == "candles":
        controls.append(
            ui.button_group(
                *[
                    choice(label, n, bin_seconds, set_bin_seconds)
                    for label, n in BIN_SECONDS.items()
                ]
            )
        )
    controls += [
        ui.checkbox("Coinbase", is_selected=show_coinbase, on_change=set_show_coinbase),
        ui.checkbox("Bitstamp", is_selected=show_bitstamp, on_change=set_show_bitstamp),
        ui.checkbox("Kraken", is_selected=show_kraken, on_change=set_show_kraken),
    ]

    return ui.flex(
        ui.flex(*controls, gap="size-200", wrap=True, align_items="center"),
        chart if chart is not None else ui.text("Select at least one exchange"),
        direction="column",
        flex_grow=1,
    )


# =============================================================================
# Create widgets
# =============================================================================

chart_interactive = tick_chart_interactive()
chart_candles = candle_chart()
chart_ticks = tick_chart()

print("[OK] 'chart_interactive', 'chart_candles' and 'chart_ticks' created")
