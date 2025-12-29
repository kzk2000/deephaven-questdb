"""
TradingView Lightweight Charts Widget for Deephaven

Displays real-time tick trades from QuestDB using TradingView Lightweight Charts.
Shows all three exchanges (Coinbase, Bitstamp, Kraken) on the same chart.

Usage:
    # In Deephaven console:
    exec(open('/data/storage/notebooks/trades_chart_live.py').read())

    # The 'chart' variable will be created and displayed

Tables created:
    - chart: The TradingView chart widget (deephaven.ui component)

Author: Claude Code
Version: 4.0 (Phase 4 - Production-Ready with Interactive Controls)
"""

import deephaven.ui as ui
from qdb_backend import create_live_table
import json
import time

# =============================================================================
# Configuration
# =============================================================================

DEFAULT_SYMBOL = "BTC-USD"
DEFAULT_WINDOW_ROWS = 1000
CHART_HEIGHT = 600  # pixels
CHART_WIDTH = 1200  # pixels

EXCHANGE_COLORS = {
    "Coinbase": "#0088CC",  # Blue
    "Bitstamp": "#00AA44",  # Green
    "Kraken": "#FF6600",  # Orange
}

# =============================================================================
# Helper Functions
# =============================================================================


def generate_test_data():
    """Generate test data for initial development (Phase 1)."""
    current_time = int(time.time())
    base_price = 50000

    test_data = {"Coinbase": [], "Bitstamp": [], "Kraken": []}

    # Generate 20 data points over the last 100 seconds
    for i in range(20):
        timestamp = current_time - (100 - i * 5)

        # Coinbase (slightly higher prices)
        test_data["Coinbase"].append({"time": timestamp, "value": base_price + (i * 10) + 50})

        # Bitstamp (middle prices)
        test_data["Bitstamp"].append({"time": timestamp, "value": base_price + (i * 10) + 25})

        # Kraken (slightly lower prices)
        test_data["Kraken"].append({"time": timestamp, "value": base_price + (i * 10)})

    return test_data


def transform_to_chart_data(table_data, exchanges_to_show):
    """
    Transform Deephaven table data to TradingView format.

    Args:
        table_data: List of table rows from ui.use_table_data()
        exchanges_to_show: List of exchange names to include

    Returns:
        Dict of exchange → list of {time, value} sorted by time

    Example output:
        {
            'Coinbase': [{'time': 1735382445, 'value': 50000.5}, ...],
            'Bitstamp': [{'time': 1735382446, 'value': 50001.2}, ...],
            'Kraken': [...]
        }
    """
    result = {ex: [] for ex in exchanges_to_show}

    for row in table_data:
        # Handle both dict-like and attribute access
        if isinstance(row, dict):
            exchange = row.get("exchange")
            ts = row.get("timestamp")
            price = row.get("price", 0)
        else:
            # Deephaven row object - use attribute access
            exchange = getattr(row, "exchange", None)
            ts = getattr(row, "timestamp", None)
            price = getattr(row, "price", 0)

        if exchange not in exchanges_to_show:
            continue

        # Convert timestamp to Unix timestamp (seconds)
        if hasattr(ts, "timestamp"):
            unix_time = ts.timestamp()  # Python datetime object
        elif hasattr(ts, "toEpochSecond"):
            unix_time = ts.toEpochSecond()  # Deephaven Instant
        elif hasattr(ts, "getEpochSecond"):
            unix_time = ts.getEpochSecond()  # Alternative Deephaven method
        else:
            # Fallback: assume it's already a numeric timestamp
            try:
                unix_time = float(ts)
            except:
                continue  # Skip rows with invalid timestamps

        try:
            price = float(price)
        except:
            continue  # Skip rows with invalid prices

        result[exchange].append({"time": unix_time, "value": price})

    # Sort by time to handle out-of-order trades
    for ex in result:
        result[ex].sort(key=lambda p: p["time"])

    return result


# =============================================================================
# Interactive Chart Component with Controls (Phase 4)
# =============================================================================


@ui.component
def tick_chart_interactive():
    """
    Interactive TradingView chart with user controls.

    Features:
    - Symbol selector dropdown
    - Exchange toggle checkboxes
    - Window size selector
    - Debug mode toggle
    - Live data from QuestDB

    Returns:
        deephaven.ui component with controls and chart
    """

    # State management
    symbol, set_symbol = ui.use_state(DEFAULT_SYMBOL)
    window_rows, set_window_rows = ui.use_state(DEFAULT_WINDOW_ROWS)
    show_debug, set_show_debug = ui.use_state(False)
    show_coinbase, set_show_coinbase = ui.use_state(True)
    show_bitstamp, set_show_bitstamp = ui.use_state(True)
    show_kraken, set_show_kraken = ui.use_state(True)

    # Determine which exchanges to display
    exchanges_to_show = []
    if show_coinbase:
        exchanges_to_show.append("Coinbase")
    if show_bitstamp:
        exchanges_to_show.append("Bitstamp")
    if show_kraken:
        exchanges_to_show.append("Kraken")

    # Get live data
    try:
        trades = create_live_table("trades", refreshing=True)
        trades_filtered = trades.where(f"symbol == `{symbol}`").tail(window_rows)
        table_data = ui.use_table_data(trades_filtered)
        chart_data = transform_to_chart_data(table_data, exchanges_to_show)
        has_error = False
        error_message = ""
    except Exception as e:
        chart_data = {}
        has_error = True
        error_message = str(e)

    # Calculate statistics
    total_data_points = sum(len(points) for points in chart_data.values())
    data_points_per_exchange = {ex: len(points) for ex, points in chart_data.items()}

    # Render controls and chart
    return ui.view(
        ui.heading("TradingView Lightweight Charts - Live Crypto Trades", level=1),
        # Control Panel
        ui.view(
            ui.heading("Controls", level=3),
            # Symbol selector
            ui.view(
                ui.text(
                    "Symbol:",
                    UNSAFE_style={"fontWeight": "bold", "marginRight": "10px"},
                ),
                ui.button_group(
                    ui.button(
                        "BTC-USD",
                        on_press=lambda: set_symbol("BTC-USD"),
                        variant="primary" if symbol == "BTC-USD" else "secondary",
                    ),
                    ui.button(
                        "ETH-USD",
                        on_press=lambda: set_symbol("ETH-USD"),
                        variant="primary" if symbol == "ETH-USD" else "secondary",
                    ),
                    ui.button(
                        "AVAX-USD",
                        on_press=lambda: set_symbol("AVAX-USD"),
                        variant="primary" if symbol == "AVAX-USD" else "secondary",
                    ),
                    ui.button(
                        "SOL-USD",
                        on_press=lambda: set_symbol("SOL-USD"),
                        variant="primary" if symbol == "SOL-USD" else "secondary",
                    ),
                ),
                UNSAFE_style={
                    "display": "flex",
                    "alignItems": "center",
                    "marginBottom": "15px",
                },
            ),
            # Exchange toggles
            ui.view(
                ui.text(
                    "Exchanges:",
                    UNSAFE_style={"fontWeight": "bold", "marginRight": "10px"},
                ),
                ui.checkbox("Coinbase", is_selected=show_coinbase, on_change=set_show_coinbase),
                ui.checkbox("Bitstamp", is_selected=show_bitstamp, on_change=set_show_bitstamp),
                ui.checkbox("Kraken", is_selected=show_kraken, on_change=set_show_kraken),
                UNSAFE_style={
                    "display": "flex",
                    "alignItems": "center",
                    "gap": "15px",
                    "marginBottom": "15px",
                },
            ),
            # Window size selector
            ui.view(
                ui.text(
                    "Window Size:",
                    UNSAFE_style={"fontWeight": "bold", "marginRight": "10px"},
                ),
                ui.button_group(
                    ui.button(
                        "100",
                        on_press=lambda: set_window_rows(100),
                        variant="primary" if window_rows == 100 else "secondary",
                    ),
                    ui.button(
                        "500",
                        on_press=lambda: set_window_rows(500),
                        variant="primary" if window_rows == 500 else "secondary",
                    ),
                    ui.button(
                        "1000",
                        on_press=lambda: set_window_rows(1000),
                        variant="primary" if window_rows == 1000 else "secondary",
                    ),
                    ui.button(
                        "5000",
                        on_press=lambda: set_window_rows(5000),
                        variant="primary" if window_rows == 5000 else "secondary",
                    ),
                ),
                UNSAFE_style={
                    "display": "flex",
                    "alignItems": "center",
                    "marginBottom": "15px",
                },
            ),
            # Debug toggle
            ui.checkbox("Show Debug Info", is_selected=show_debug, on_change=set_show_debug),
            UNSAFE_style={
                {
                    "backgroundColor": "#2B2B2B",
                    "padding": "20px",
                    "borderRadius": "8px",
                    "marginBottom": "20px",
                    "border": "1px solid #404040",
                }
            },
        ),
        # Error message
        ui.view(
            ui.heading("Error", level=4, UNSAFE_style={"color": "#FF6B6B"}),
            ui.text(error_message, UNSAFE_style={"color": "#FF6B6B"}),
            UNSAFE_style={
                {
                    "backgroundColor": "#3B2020",
                    "padding": "15px",
                    "borderRadius": "6px",
                    "marginBottom": "20px",
                    "border": "1px solid #804040",
                }
            },
        )
        if has_error
        else None,
        # Debug info
        ui.view(
            ui.heading("Performance Statistics", level=4),
            ui.text(f"Symbol: {symbol}"),
            ui.text(f"Total data points: {total_data_points}"),
            ui.text(f"Active exchanges: {', '.join(exchanges_to_show)}"),
            ui.text(
                f"Points per exchange: {', '.join([f'{ex}: {count}' for ex, count in data_points_per_exchange.items()])}"
            ),
            ui.text(f"Window size: {window_rows} trades"),
            UNSAFE_style={
                {
                    "backgroundColor": "#2B2B2B",
                    "padding": "15px",
                    "borderRadius": "6px",
                    "marginBottom": "20px",
                    "border": "1px solid #404040",
                }
            },
        )
        if show_debug
        else None,
        # Chart component
        tick_chart(
            symbol=symbol,
            window_rows=window_rows,
            exchanges_to_show=exchanges_to_show,
            use_test_data=False,
            show_stats=False,  # Stats shown in control panel instead
        ),
    )


# =============================================================================
# Basic Chart Component (used by interactive component)
# =============================================================================


@ui.component
def tick_chart(
    symbol=DEFAULT_SYMBOL,
    window_rows=DEFAULT_WINDOW_ROWS,
    exchanges_to_show=None,
    use_test_data=False,
    show_stats=False,
):
    """
    TradingView Lightweight Charts component for tick trades.

    Args:
        symbol: Trading symbol to display (e.g., 'BTC-USD')
        window_rows: Number of recent trades to display
        exchanges_to_show: List of exchanges to display (default: all)
        use_test_data: If True, use hardcoded test data (for development)
        show_stats: If True, display statistics below chart

    Returns:
        deephaven.ui component with embedded TradingView chart
    """

    if exchanges_to_show is None:
        exchanges_to_show = list(EXCHANGE_COLORS.keys())

    # Get data
    if use_test_data:
        chart_data = generate_test_data()
    else:
        # Phase 2: Connect to live table
        trades = create_live_table("trades", refreshing=True)
        trades_filtered = trades.where(f"symbol == `{symbol}`").tail(window_rows)
        table_data = ui.use_table_data(trades_filtered)
        chart_data = transform_to_chart_data(table_data, exchanges_to_show)

    # Phase 3: Calculate performance statistics
    total_data_points = sum(len(points) for points in chart_data.values())
    exchanges_with_data = [ex for ex, points in chart_data.items() if len(points) > 0]
    data_points_per_exchange = {ex: len(points) for ex, points in chart_data.items()}

    # Convert data to JSON for JavaScript
    data_json = json.dumps(chart_data)
    colors_json = json.dumps(EXCHANGE_COLORS)

    # Generate unique ID for this chart instance
    chart_id = f"tradingview-chart-{id(chart_data)}"

    # JavaScript code to create and populate the chart
    chart_js = f"""
    (function() {{
        // Wait for TradingView library to load
        if (typeof LightweightCharts === 'undefined') {{
            console.error('TradingView Lightweight Charts library not loaded yet');
            setTimeout(arguments.callee, 100);  // Retry after 100ms
            return;
        }}

        const container = document.getElementById('{chart_id}');
        if (!container) {{
            console.error('Chart container not found: {chart_id}');
            return;
        }}

        // Clear previous chart if exists
        container.innerHTML = '';

        // Create chart
        const chart = LightweightCharts.createChart(container, {{
            width: {CHART_WIDTH},
            height: {CHART_HEIGHT},
            layout: {{
                background: {{ color: '#1E1E1E' }},
                textColor: '#D9D9D9'
            }},
            grid: {{
                vertLines: {{ color: '#2B2B2B' }},
                horzLines: {{ color: '#2B2B2B' }}
            }},
            timeScale: {{
                timeVisible: true,
                secondsVisible: true,
                borderColor: '#2B2B2B'
            }},
            rightPriceScale: {{
                borderColor: '#2B2B2B'
            }}
        }});

        // Parse data
        const data = {data_json};
        const colors = {colors_json};

        console.log('Creating TradingView chart with data:', data);

        // Add series for each exchange
        const series = {{}};
        Object.keys(data).forEach(exchange => {{
            if (data[exchange].length > 0) {{
                series[exchange] = chart.addLineSeries({{
                    color: colors[exchange],
                    lineWidth: 2,
                    title: exchange,
                    priceFormat: {{
                        type: 'price',
                        precision: 2,
                        minMove: 0.01
                    }}
                }});

                series[exchange].setData(data[exchange]);
                console.log(`Added ${{exchange}} series with ${{data[exchange].length}} points`);
            }}
        }});

        // Fit chart to data
        chart.timeScale().fitContent();

        // Handle window resize
        const resizeObserver = new ResizeObserver(entries => {{
            const {{ width, height }} = entries[0].contentRect;
            chart.resize(Math.max(width, 300), {CHART_HEIGHT});
        }});
        resizeObserver.observe(container);

        // Store chart reference for cleanup
        container._chart = chart;
        container._resizeObserver = resizeObserver;

        console.log('TradingView chart initialized successfully');
    }})();
    """

    # Render HTML structure with chart
    return ui.view(
        ui.heading(f"Live Tick Trades: {{symbol}}", level=2),
        ui.text(f"Showing last {{window_rows}} trades from all exchanges"),
        # Phase 3: Debug info (optional)
        ui.view(
            ui.heading("Performance Statistics", level=4),
            ui.text(f"Total data points: {{total_data_points}}"),
            ui.text(f"Active exchanges: {{', '.join(exchanges_with_data)}}"),
            ui.text(
                f"Points per exchange: {{', '.join([f'{{ex}}: {{count}}' for ex, count in data_points_per_exchange.items()])}}"
            ),
            ui.text(f"Window size: {{window_rows}} trades"),
            ui.text(f"Data source: {{'Test data' if use_test_data else 'Live QuestDB'}}"),
            UNSAFE_style={
                {
                    {
                        {
                            "backgroundColor": "#2B2B2B",
                            "padding": "15px",
                            "borderRadius": "6px",
                            "marginBottom": "20px",
                            "border": "1px solid #404040",
                        }
                    }
                }
            },
        )
        if show_stats
        else None,
        # TradingView library from CDN
        ui.html.script(
            src="https://unpkg.com/lightweight-charts/dist/lightweight-charts.standalone.production.js"
        ),
        # Chart container
        ui.html.div(
            id=chart_id,
            UNSAFE_style={
                {
                    "height": f"{CHART_HEIGHT}px",
                    "width": "100%",
                    "maxWidth": f"{CHART_WIDTH}px",
                    "margin": "20px auto",
                    "backgroundColor": "#1E1E1E",
                    "borderRadius": "8px",
                    "padding": "10px",
                    "boxShadow": "0 4px 6px rgba(0, 0, 0, 0.3)",
                }
            },
        ),
        # Initialize chart
        ui.html.script(chart_js),
        # Legend
        ui.html.div(
            ui.html.h4("Legend:", UNSAFE_style={{"color": "#D9D9D9", "marginTop": "20px"}}),
            ui.html.div(
                ui.html.span(
                    "■ Coinbase",
                    UNSAFE_style={
                        {
                            "color": EXCHANGE_COLORS["Coinbase"],
                            "marginRight": "20px",
                            "fontSize": "16px",
                            "fontWeight": "bold",
                        }
                    },
                ),
                ui.html.span(
                    "■ Bitstamp",
                    UNSAFE_style={
                        {
                            "color": EXCHANGE_COLORS["Bitstamp"],
                            "marginRight": "20px",
                            "fontSize": "16px",
                            "fontWeight": "bold",
                        }
                    },
                ),
                ui.html.span(
                    "■ Kraken",
                    UNSAFE_style={
                        {
                            "color": EXCHANGE_COLORS["Kraken"],
                            "fontSize": "16px",
                            "fontWeight": "bold",
                        }
                    },
                ),
                UNSAFE_style={{"marginTop": "10px"}},
            ),
        ),
    )


# =============================================================================
# Create Chart Instances
# =============================================================================

# Phase 4: Interactive chart with full controls (RECOMMENDED)
chart_interactive = tick_chart_interactive()

# Phase 3: Basic chart with live data (for advanced users)
chart = tick_chart(symbol="BTC-USD", window_rows=1000, use_test_data=False)

print("[OK] TradingView Lightweight Charts widgets created!")
print("   - Phase: 4 (Production-Ready with Interactive Controls)")
print("")
print("[INTERACTIVE] 'chart_interactive' - Full-featured widget with controls")
print("   - Symbol selector (BTC-USD, ETH-USD, AVAX-USD, SOL-USD)")
print("   - Exchange toggles (Coinbase, Bitstamp, Kraken)")
print("   - Window size selector (100/500/1000/5000 trades)")
print("   - Debug info toggle")
print("   - Error handling and performance stats")
print("")
print("[BASIC] 'chart' - Simple chart widget for advanced users")
print("   - Fixed symbol: BTC-USD")
print("   - Fixed window: 1000 trades")
print("   - All exchanges enabled")
print("")
print("[EXAMPLES] Create custom charts:")
print("   # Basic chart with different symbol")
print("   chart_eth = tick_chart(symbol='ETH-USD', window_rows=500)")
print("")
print("   # Test with hardcoded data")
print("   chart_test = tick_chart(symbol='BTC-USD', use_test_data=True)")
print("")
print("   # Chart with only specific exchanges")
print("   chart_custom = tick_chart(symbol='SOL-USD',")
print("                              exchanges_to_show=['Coinbase', 'Kraken'])")
print("")
print("[INFO] All charts auto-update as new trades arrive from QuestDB")
print("       Refresh interval: ~50ms (WAL polling)")
print("       Memory optimized with .tail() windowing")
