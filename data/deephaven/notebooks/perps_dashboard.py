"""
Perpetual-futures liquidity desk: Deribit against the other venues, live from QuestDB.

Headline: bid-ask spread, USD resting within 1..100 bp of mid and market-order cost for
$10k..$5M, per asset (BTC / ETH) and family:
    inverse = coin-margined -USD-PERP (Deribit, OKX, Bybit)
    linear  = -USDT-PERP and -USDC-PERP, USDT/USDC treated as USD (Deribit and Hyperliquid
              list USDC books, OKX / Binance / Bybit USDT)
Venues come from perps_dash_lib.VENUES (data, not layout): a venue without data shows as a grey NO DATA row and lights up when rows
arrive. Coverage matters: a venue's published book reaches only so far from mid (OKX 400
levels, Hyperliquid 20 plus aggregated books, Bybit 1000, Binance 20), so wider buckets are lower bounds, shown as ">= ..." in amber.

Usage:
    exec(open('/data/storage/notebooks/perps_dashboard.py').read())

Widget created:
    - perps_dashboard (http://localhost:10000/iframe/widget/?name=perps_dashboard)

Other top-level names are prefixed `pd_` so they do not collide with other notebooks.

Data flow: QuestDB does the compute, Deephaven only holds small state tables.
    Every input is a qdb_feed.feed_query: QuestDB re-runs the SQL every poll and only rows
    whose values changed are published, so a table ticks only when something moved.
      latest liquidity    perps_liquidity LATEST ON ...            ~10 rows, 1 s
      rolling windows     GROUP BY over perps_trades_10s (1/5/15m)  ~30 rows, 1 s
      spot reference      GROUP BY over spot_trades_10s             ~6 rows, 2 s
      10 s series         closed buckets of perps_trades_10s, 15 min ~1k rows, 5 s
      liquidity history   closed minutes of perps_liquidity_1m, 1 h ~600 rows, 10 s
    Feeds are kept on the qdb_feed module, so re-running the notebook reuses them instead of
    starting more polling threads. Derived tables live in `pd_scope`, released on re-run.
    The component tree is split per tab, only the visible tab renders, and cards / matrices
    are memoized on their displayed strings. Plotly charts redraw at most every 5-10 s (each
    redraw costs the browser 100+ ms), the TradingView price chart and the cards tick faster.
"""

import importlib
import time

import deephaven.plot.express as dx
import deephaven.plot.tradingview_lightweight as tvl
import deephaven.ui as ui
import numpy as np
import perps_dash_lib
import qdb_feed
from deephaven import agg, new_table, time_table
from deephaven.column import double_col, int_col, string_col
from deephaven.liveness_scope import LivenessScope

# Re-running the notebook should pick up edits to the helper module
pd_lib = importlib.reload(perps_dash_lib)

# Release every derived table of the previous run (otherwise each re-run would leave a full
# copy of the pipeline ticking in the update graph)
try:
    pd_scope.release()  # noqa: F821 (defined by the previous run)
except NameError:
    pass
pd_scope = LivenessScope()

# =============================================================================
# Configuration
# =============================================================================

PD_WINDOWS = {"1m": 1, "5m": 5, "15m": 15}  # rolling volume windows, minutes
PD_SERIES_MINUTES = 15  # 10 s price / volume series kept for the charts
PD_STALE_SECONDS = 15.0  # default; per-venue values come from perps_dash_lib.VENUES
PD_LIQ_HISTORY_MIN = 60
# Cadence of the snapshot-style plotly charts. Every plotly redraw rebuilds its SVG (hundreds
# of DOM nodes, 50-150 ms of main thread), so these charts refresh slowly; the matrices above
# them carry the live numbers.
PD_CHART_REFRESH = "PT30S"
PD_REF_EXCHANGE = "DERIBIT"  # the venue every other venue is compared against
PD_FAMILIES = [(a, c) for a in pd_lib.ASSETS for c in pd_lib.CONTRACT_TYPES]

# Colours: Deephaven theme tokens for text (follow light / dark), fixed hex per venue in charts
PD_UP, PD_DOWN, PD_WARN, PD_MUTED = "positive", "negative", "notice", "gray-600"
PD_VENUE_COLORS = {
    "Deribit": ("#4f9cf9", "#4f9cf9"),
    "OKX": ("#c084fc", "#e9d5ff"),
    "Hyperliquid": ("#34d399", "#34d399"),
    "Binance": ("#f0b90b", "#f8dc7a"),
    "Bybit": ("#fb923c", "#fdba74"),
}
PD_SPOT_COLOR = "#8a8f98"
PD_REF_BG = "rgba(79, 156, 249, 0.13)"
PD_CMP_BG = "rgba(128, 128, 128, 0.10)"
PD_LB = "≥ "  # lower-bound prefix

_pd_rows_ref = pd_lib.instrument_rows()
pd_series_colors = {
    r["Series"]: PD_VENUE_COLORS.get(r["Venue"], ("#888888", "#bbbbbb"))[r["Quote"] == "USDC"]
    for r in _pd_rows_ref
}
_PD_SPOT_SYMBOLS = "'BTC-USD', 'ETH-USD'"
_PD_SPOT_VENUES = ", ".join(f"'{e}'" for e in pd_lib.SPOT_REFERENCE_EXCHANGES)


def _pd_family_filter(asset, ctype):
    return [f"Asset == `{asset}`", f"Ctype == `{ctype}`"]


# =============================================================================
# QuestDB feeds (persist across re-runs of this notebook)
# =============================================================================


def pd_feed(name, sql, keys, poll, capacity=None):
    """A qdb_feed.feed_query state table, reused across notebook re-runs.

    The feeds and their polling threads are stored on the qdb_feed module (which survives
    exec) inside a scope that is never released. If the SQL changed, the old feed's poller is
    turned into a no-op (feed threads cannot be stopped) and a new feed is created.
    """
    registry = qdb_feed.__dict__.setdefault("_pd_feeds", {})
    scope = qdb_feed.__dict__.setdefault("_pd_feed_scope", LivenessScope())
    spec = (sql, tuple(keys), poll, capacity)
    old = registry.get(name)
    if old is not None and old[0] == spec:
        return old[1]
    if old is not None:
        old[1].feed.step = lambda: 0  # stop the old poller's QuestDB queries
    with scope.open():
        table = qdb_feed.feed_query(
            sql, key_columns=list(keys), poll_seconds=poll, capacity=capacity, name=name
        )
    registry[name] = (spec, table)
    return table


def _pd_windows_sql():
    """One row per instrument with a trade in the last hour, with every window's figures as
    columns. A GROUP BY per window would drop an instrument that went quiet, and the feed
    would then keep showing its last (stale) row; conditional sums return 0 instead.
    Buckets are 10 s, so a window is [floor(now, 10 s) - N min, now] (N min + open bucket)."""
    cols = []
    for label, minutes in PD_WINDOWS.items():
        cond = f"timestamp >= dateadd('m', -{minutes}, timestamp_floor('10s', now()))"
        for out, expr in (
            ("open", "open"),
            ("usd", "notional_usd"),
            ("base", "volume_base"),
            ("buy", "buy_notional_usd"),
            ("sell", "sell_notional_usd"),
            ("trades", "trades"),
        ):
            if out == "open":  # price at the start of the window: first bucket inside it
                cols.append(f"first_not_null(CASE WHEN {cond} THEN open END) AS open_{label}")
            else:
                zero = "0" if out == "trades" else "0.0"
                cols.append(f"sum(CASE WHEN {cond} THEN {expr} ELSE {zero} END) AS {out}_{label}")
    return f"""
        SELECT exchange, symbol, last(close) AS last_px, max(timestamp) AS last_bucket,
            {", ".join(cols)}
        FROM perps_trades_10s
        WHERE timestamp >= dateadd('h', -1, now())
        GROUP BY exchange, symbol"""


pd_liq = pd_feed(
    "pd_liq",
    "SELECT * FROM perps_liquidity LATEST ON timestamp PARTITION BY exchange, symbol",
    ["exchange", "symbol"],
    1.0,
)
pd_win = pd_feed("pd_win", _pd_windows_sql(), ["exchange", "symbol"], 1.0)
pd_spot = pd_feed(
    "pd_spot",
    f"""SELECT exchange, symbol, last(close) AS close, max(timestamp) AS last_bucket
        FROM spot_trades_10s
        WHERE symbol IN ({_PD_SPOT_SYMBOLS}) AND exchange IN ({_PD_SPOT_VENUES})
            AND timestamp > dateadd('m', -10, now())
        GROUP BY exchange, symbol""",
    ["exchange", "symbol"],
    2.0,
)
# Closed buckets only (timestamp < the open bucket): each bucket is published once, so the
# series tables and their charts change once per 10 s instead of every poll
pd_series = pd_feed(
    "pd_series",
    f"""SELECT timestamp, exchange, symbol, close, notional_usd, buy_notional_usd,
            sell_notional_usd, trades
        FROM perps_trades_10s
        WHERE timestamp > dateadd('m', -{PD_SERIES_MINUTES}, now())
            AND timestamp < timestamp_floor('10s', now())""",
    ["timestamp", "exchange", "symbol"],
    5.0,
    capacity=2000,  # ~18 instruments x 6 buckets/min x 15 min; the ring rolls old buckets out
)
pd_spot_series = pd_feed(
    "pd_spot_series",
    f"""SELECT timestamp, symbol, close FROM spot_trades_10s
        WHERE exchange = 'COINBASE' AND symbol IN ({_PD_SPOT_SYMBOLS})
            AND timestamp > dateadd('m', -{PD_SERIES_MINUTES}, now())
            AND timestamp < timestamp_floor('10s', now())""",
    ["timestamp", "symbol"],
    5.0,
    capacity=200,
)
_pd_hist_cols = ["spread_bps"] + [
    pd_lib.depth_col(s, b) for s in ("bid", "ask") for b in pd_lib.BUCKETS_BPS
]
pd_liq_hist = pd_feed(
    "pd_liq_hist",
    f"""SELECT timestamp, exchange, symbol, {", ".join(_pd_hist_cols)}
        FROM perps_liquidity_1m
        WHERE timestamp > dateadd('m', -{PD_LIQ_HISTORY_MIN}, now())
            AND timestamp < timestamp_floor('m', now())""",
    ["timestamp", "exchange", "symbol"],
    10.0,
    capacity=1200,
)

# =============================================================================
# Derived tables: joins over the small state tables only (built once per exec, in pd_scope)
# =============================================================================

with pd_scope.open():
    pd_registry = new_table(
        [
            string_col(c, [r[c] for r in _pd_rows_ref])
            for c in ["exchange", "symbol", "Venue", "Series", "Asset", "Quote", "Ctype", "Note"]
        ]
        + [int_col("VenueOrder", [r["VenueOrder"] for r in _pd_rows_ref])]
        + [double_col("StaleAfter", [r["StaleAfter"] for r in _pd_rows_ref])]
    )
    _pd_keys = ["exchange", "symbol"]
    _pd_series_cols = ["Series", "Asset", "Ctype", "VenueOrder"]

    # One small table per family, all venues kept (NO DATA rows have NULL metrics)
    pd_family_book = {
        (a, c): pd_registry.where(_pd_family_filter(a, c))
        .natural_join(pd_liq, on=_pd_keys)
        .sort(["VenueOrder", "symbol"])
        for a, c in PD_FAMILIES
    }

    pd_spot_ref = pd_spot.update_view(["Asset = symbol.substring(0, 3)"]).agg_by(
        [agg.median("SpotRef = close")], by=["Asset"]
    )

    def _pd_market(label):
        t = (
            pd_registry.natural_join(
                pd_win.view(
                    [
                        "exchange",
                        "symbol",
                        "last_px",
                        "last_bucket",
                        f"open_px = open_{label}",
                        f"usd_vol = usd_{label}",
                        f"base_vol = base_{label}",
                        f"buy_usd = buy_{label}",
                        f"sell_usd = sell_{label}",
                        f"trades = trades_{label}",
                    ]
                ),
                on=_pd_keys,
            )
            .natural_join(
                pd_liq,
                on=_pd_keys,
                joins=["BookTs = timestamp", "Mid = mid", "SpreadBps = spread_bps"],
            )
            .natural_join(pd_spot_ref, on=["Asset"], joins=["SpotRef"])
            .update_view(
                [
                    # Mid is the better fair price; fall back to the last trade without a book
                    "RefPx = isNull(Mid) ? last_px : Mid",
                    "ChgBps = (last_px / open_px - 1) * 1e4",  # bp: % rounds to 0.00 over minutes
                    "PremSpotBps = (RefPx / SpotRef - 1) * 1e4",
                    "BuyShare = usd_vol > 0 ? buy_usd / usd_vol : NULL_DOUBLE",
                ]
            )
        )
        # Cross-venue difference: each venue against the median of its family
        fam = t.where("!isNull(RefPx)").agg_by([agg.median("FamPx = RefPx")], by=["Asset", "Ctype"])
        return t.natural_join(fam, on=["Asset", "Ctype"]).update_view(
            ["VsFamBps = (RefPx / FamPx - 1) * 1e4"]
        )

    _pd_market_all = {label: _pd_market(label) for label in PD_WINDOWS}
    pd_family_market = {
        (a, c, w): _pd_market_all[w].where(_pd_family_filter(a, c)).sort(["VenueOrder", "symbol"])
        for a, c in PD_FAMILIES
        for w in PD_WINDOWS
    }

    # Snapshot-style charts redraw at most every PD_CHART_REFRESH
    _pd_chart_clock = time_table(PD_CHART_REFRESH)
    pd_liq_chart = pd_liq.snapshot_when(_pd_chart_clock).drop_columns(["Timestamp"])
    pd_series_named = pd_series.natural_join(pd_registry, on=_pd_keys, joins=_pd_series_cols)
    pd_hist_named = pd_liq_hist.natural_join(pd_registry, on=_pd_keys, joins=_pd_series_cols)

# =============================================================================
# Charts and views: built on first use, cached for the life of this exec (inside pd_scope).
# A cached object keeps its identity across renders, so the browser never re-mounts a chart.
# =============================================================================

_pd_cache = {}


def pd_cached(key, builder):
    obj = _pd_cache.get(key)
    if obj is None:
        with pd_scope.open():
            obj = builder()
        _pd_cache[key] = obj
    return obj


def _pd_family_join(asset, ctype):
    # Only venues with data, refreshed every PD_CHART_REFRESH
    return pd_registry.where(_pd_family_filter(asset, ctype)).join(pd_liq_chart, on=_pd_keys)


def _pd_side_expr(side, bps):
    if side == "bid":
        return pd_lib.depth_col("bid", bps), "bid_reach_bps"
    if side == "ask":
        return pd_lib.depth_col("ask", bps), "ask_reach_bps"
    both = f"{pd_lib.depth_col('bid', bps)} + {pd_lib.depth_col('ask', bps)}"
    return both, "Math.min(bid_reach_bps, ask_reach_bps)"


def pd_bucket_chart(asset, ctype, side):
    """USD within N bp of mid per venue and bucket; hatched bars are lower bounds."""
    buckets = pd_lib.BUCKETS_BPS
    exprs = [_pd_side_expr(side, b) for b in buckets]
    # One view building per-row arrays + one ungroup (cheaper than one view per bucket)
    long = (
        _pd_family_join(asset, ctype)
        .view(
            [
                "Series",
                "VenueOrder",
                "Bucket = new String[] {" + ", ".join(f"`{b} bp`" for b in buckets) + "}",
                "BucketBps = new int[] {" + ", ".join(str(b) for b in buckets) + "}",
                "Usd = new double[] {" + ", ".join(e for e, _ in exprs) + "}",
                "Coverage = new String[] {"
                + ", ".join(
                    f"!isNull({r}) && {r} >= {b} ? `complete` : `lower bound`"
                    for b, (_, r) in zip(buckets, exprs)
                )
                + "}",
            ]
        )
        .ungroup(["Bucket", "BucketBps", "Usd", "Coverage"])
        .sort(["BucketBps", "VenueOrder"])
    )
    return dx.bar(
        long,
        x="Bucket",
        y="Usd",
        color="Series",
        pattern_shape="Coverage",
        pattern_shape_map={"complete": "", "lower bound": "/"},
        color_discrete_map=pd_series_colors,
        barmode="group",
        log_y=True,
        title=f"USD within N bp of mid ({side}; hatched = lower bound; log; 30 s refresh)",
        labels={"Bucket": "distance from mid", "Usd": "USD notional", "Series": "venue"},
    )


def pd_slippage_curve_chart(asset, ctype):
    """Slippage vs order size per venue, buys solid and sells dashed; gaps = book exhausted."""
    pairs = [(d, k, n) for d in ("buy", "sell") for k, n in pd_lib.NOTIONALS.items()]
    long = (
        _pd_family_join(asset, ctype)
        .view(
            [
                "Series",
                "Notional = new double[] {" + ", ".join(str(float(n)) for _, _, n in pairs) + "}",
                "Side = new String[] {" + ", ".join(f"`{d}`" for d, _, _ in pairs) + "}",
                "SlipBps = new double[] {"
                + ", ".join(pd_lib.slip_col(d, k) for d, k, _ in pairs)
                + "}",
            ]
        )
        .ungroup(["Notional", "Side", "SlipBps"])
        .sort(["Series", "Side", "Notional"])
    )
    return dx.line(
        long,
        x="Notional",
        y="SlipBps",
        by=["Series", "Side"],
        color="Series",
        line_dash="Side",
        markers=True,
        log_x=True,
        color_discrete_map=pd_series_colors,
        title="Market order cost vs mid (bp); missing point = more than the book; 30 s refresh",
        labels={"Notional": "order notional (USD, log)", "SlipBps": "bp vs mid", "Series": "venue"},
    )


def pd_history_chart(asset, ctype, metric):
    """1-minute means (closed minutes) from perps_liquidity_1m: 'spread' or a bucket (int bp)."""
    h = pd_hist_named.where(_pd_family_filter(asset, ctype))
    if metric == "spread":
        y, y_label, log_y = "spread_bps", "spread (bp)", False
        title = "Bid-ask spread, 1-minute mean (bp)"
        h = h.view(["timestamp", "Series", "spread_bps"])
    else:
        y, y_label, log_y = "DepthUsd", "USD", True
        title = f"USD within ±{metric} bp of mid (bid + ask), 1-minute mean"
        bid, ask = pd_lib.depth_col("bid", metric), pd_lib.depth_col("ask", metric)
        h = h.view(["timestamp", "Series", f"DepthUsd = {bid} + {ask}"])
    return dx.line(
        h.sort(["Series", "timestamp"]),
        x="timestamp",
        y=y,
        by="Series",
        log_y=log_y,
        color_discrete_map=pd_series_colors,
        title=f"{title}, last {PD_LIQ_HISTORY_MIN} min"
        + ("" if metric == "spread" else " (lower bound beyond a venue's book coverage)"),
        labels={"timestamp": "time", y: y_label, "Series": "venue"},
    )


def _pd_family_series(asset, ctype):
    return pd_series_named.where(_pd_family_filter(asset, ctype))


def pd_price_chart(asset, ctype):
    """10 s closes per venue plus the Coinbase spot close (TradingView, canvas based).

    One line per registry series so each venue keeps its dashboard colour (a `by=` line would
    get automatic colours); a series without data is simply an empty line."""
    perps = _pd_family_series(asset, ctype).sort(["timestamp"])
    lines = [
        tvl.line(
            perps.where(f"Series == `{r['Series']}`"),
            timestamp="timestamp",
            value="close",
            title=r["Series"],
            color=pd_series_colors[r["Series"]],
            line_width=2,
        )
        for r in pd_lib.family_rows(asset, ctype)
    ]
    spot = pd_spot_series.where(f"symbol == `{asset}-USD`").sort(["timestamp"])
    lines.append(
        tvl.line(
            spot,
            timestamp="timestamp",
            value="close",
            title="Coinbase spot",
            color=PD_SPOT_COLOR,
            line_width=1,
        )
    )
    return tvl.chart(*lines, time_scale=tvl.time_scale(time_visible=True, seconds_visible=True))


def pd_premium_chart(asset, ctype):
    """10 s close of each perp vs the Coinbase spot close of the same bucket, in bp."""
    spot = pd_spot_series.where(f"symbol == `{asset}-USD`").view(["timestamp", "SpotPx = close"])
    prem = (
        _pd_family_series(asset, ctype)
        .natural_join(spot, on=["timestamp"])
        .where("!isNull(SpotPx)")
        .view(["timestamp", "Series", "PremBps = (close / SpotPx - 1) * 1e4"])
        .sort(["Series", "timestamp"])
    )
    return dx.line(
        prem,
        x="timestamp",
        y="PremBps",
        by="Series",
        color_discrete_map=pd_series_colors,
        title="Perp premium vs Coinbase spot (bp, 10 s closes)",
        labels={"timestamp": "time", "PremBps": "premium (bp)", "Series": "venue"},
    )


def pd_volume_chart(asset, ctype):
    bars = _pd_family_series(asset, ctype).view(["timestamp", "Series", "notional_usd"])
    return dx.bar(
        bars.sort(["timestamp", "Series"]),
        x="timestamp",
        y="notional_usd",
        by="Series",
        color_discrete_map=pd_series_colors,
        title=f"Notional per 10 s by venue (USD, last {PD_SERIES_MINUTES} min)",
        labels={"timestamp": "time", "notional_usd": "USD notional", "Series": "venue"},
    )


def pd_flow_chart(asset, ctype, window):
    # Sell volume plotted negative: a diverging bar reads as buy vs sell pressure at a glance
    flow = (
        pd_family_market[(asset, ctype, window)]
        .where("!isNull(usd_vol)")
        .view(["Series", "Buy = buy_usd", "Sell = -sell_usd"])
        .snapshot_when(_pd_chart_clock)
    )
    return dx.bar(
        flow,
        x="Series",
        y=["Buy", "Sell"],
        title=f"Aggressor flow, last {window} (USD; sells negative; 30 s refresh)",
        labels={"Series": "venue", "value": "USD notional", "variable": "side"},
        color_discrete_sequence=["#22c55e", "#ef4444"],
    )


def pd_market_view(asset, ctype, window):
    return pd_family_market[(asset, ctype, window)].view(
        [
            "Series",
            "symbol",
            "LastPx = last_px",
            "ChgBps",
            "Mid",
            "SpreadBps",
            "PremSpotBps",
            "VsFamBps",
            "UsdVol = usd_vol",
            "BaseVol = base_vol",
            "Trades = trades",
            "BuyUsd = buy_usd",
            "SellUsd = sell_usd",
            "LastBucket = last_bucket",
        ]
    )


# =============================================================================
# Row helpers (pure Python on the small family tables)
# =============================================================================


def _pd_num(x):
    """Row value -> float or None (pandas gives NaN / NaT / None for Deephaven NULL)."""
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def _pd_age_s(ts, now_ns):
    try:
        if ts is None or ts != ts:  # None or NaT
            return None
        return (now_ns - ts.value) / 1e9
    except (AttributeError, TypeError, ValueError):
        return None


def _pd_rows(data):
    """use_table_data dict-of-lists -> list of row dicts (empty when no data yet)."""
    if not data:
        return []
    cols = list(data.keys())
    return [dict(zip(cols, vals)) for vals in zip(*(data[c] for c in cols))]


def _pd_status(row, ts_col, now_ns):
    stale_after = _pd_num(row.get("StaleAfter")) or PD_STALE_SECONDS
    return pd_lib.venue_status(_pd_age_s(row.get(ts_col), now_ns), None, stale_after)


def _pd_reach(row, side):
    if side == "bid":
        return _pd_num(row.get("bid_reach_bps"))
    if side == "ask":
        return _pd_num(row.get("ask_reach_bps"))
    b, a = _pd_num(row.get("bid_reach_bps")), _pd_num(row.get("ask_reach_bps"))
    return None if b is None or a is None else min(b, a)


def _pd_depth(row, side, bucket):
    """(USD within `bucket` bp on `side`, is_lower_bound) for one liquidity row."""
    if side == "both":
        b = _pd_num(row.get(pd_lib.depth_col("bid", bucket)))
        a = _pd_num(row.get(pd_lib.depth_col("ask", bucket)))
        val = None if b is None or a is None else b + a
    else:
        val = _pd_num(row.get(pd_lib.depth_col(side, bucket)))
    return val, pd_lib.is_lower_bound(_pd_reach(row, side), bucket)


def _pd_fmt_reach(reach):
    if reach is None:
        return "-"
    return f"{reach:,.0f} bp" if reach >= 100 else f"{reach:,.1f} bp"


def _pd_signed_color(x, eps=0.0):
    if x is None:
        return PD_MUTED
    if x > eps:
        return PD_UP
    if x < -eps:
        return PD_DOWN
    return None


# =============================================================================
# Presentational components. memo=True: re-rendered only when their (string) props change,
# so a card whose numbers did not move is skipped entirely.
# =============================================================================

_PD_STATUS_BADGE = {"LIVE": "positive", "STALE": "yellow", "NO DATA": "neutral"}
_PD_NUM_STYLE = {"fontVariantNumeric": "tabular-nums"}
_PD_NOTE_STYLE = {"fontSize": "12px"}


# Not memoized: a memoized component that is skipped on a parent re-render loses its event
# handlers (clicks stop working), so only callback-free components use memo=True
@ui.component
def pd_segmented(label, options, value, on_change):
    """A labelled single-select segmented control (Spectrum ActionGroup)."""

    def handle(keys):
        keys = list(keys) if not isinstance(keys, str) else [keys]
        if keys:
            on_change(keys[0])

    return ui.flex(
        ui.text(label, color=PD_MUTED),
        ui.action_group(
            *[ui.item(o, key=o) for o in options],
            selection_mode="single",
            disallow_empty_selection=True,
            selected_keys=[value],
            on_selection_change=handle,
            density="compact",
            is_emphasized=True,
        ),
        direction="column",
        gap="size-25",
    )


@ui.component(memo=True)
def pd_stat(label, value, sub, color):
    """Summary-strip KPI tile."""
    return ui.view(
        ui.flex(
            ui.text(label, color=PD_MUTED),
            ui.text(
                value,
                color=color,
                UNSAFE_style={"fontSize": "20px", "fontWeight": 600, **_PD_NUM_STYLE},
            ),
            ui.text(sub or " ", color=PD_MUTED),
            direction="column",
            gap="size-0",
        ),
        padding_x="size-200",
        padding_y="size-100",
        border_start_width="thick",
        border_start_color="accent-600" if color is None else color,
        background_color="gray-75",
        border_radius="small",
        min_width="size-2400",
        flex_grow=1,
    )


def pd_kv(label, value, color=None, help_text=None):
    """One label / value line inside a card, value right aligned with tabular digits."""
    label_el = ui.text(label, color=PD_MUTED)
    if help_text:
        label_el = ui.flex(
            label_el,
            ui.contextual_help(ui.heading(label), ui.content(help_text), variant="info"),
            align_items="center",
            gap="size-0",
        )
    return ui.flex(
        label_el,
        ui.text(value, color=color, UNSAFE_style=_PD_NUM_STYLE),
        justify_content="space-between",
        align_items="center",
    )


def _pd_card_frame(children, is_ref=False, muted=False):
    return ui.view(
        ui.flex(*children, direction="column", gap="size-50"),
        padding="size-150",
        border_radius="medium",
        min_width="size-2400",
        flex_grow=1,
        flex_basis="size-2400",
        UNSAFE_style={
            "background": PD_REF_BG if is_ref else "rgba(128,128,128,0.06)",
            "border": "2px solid #4f9cf9" if is_ref else "1px solid rgba(128,128,128,0.3)",
            "opacity": 0.6 if muted else 1,
        },
    )


def _pd_card_head(series, symbol, status):
    return ui.flex(
        ui.flex(
            ui.text(series, UNSAFE_style={"fontWeight": 700, "fontSize": "15px"}),
            ui.text(symbol, color=PD_MUTED, UNSAFE_style={"fontSize": "12px"}),
            direction="column",
            gap="size-0",
        ),
        ui.badge(status, variant=_PD_STATUS_BADGE.get(status, "neutral")),
        justify_content="space-between",
        align_items="start",
    )


@ui.component(memo=True)
def pd_venue_tile(series, symbol, status, is_ref, note, mid, spread, reach, reach_ok, exact):
    head = _pd_card_head(series, symbol, status)
    if status == "NO DATA":
        return _pd_card_frame([head, ui.text(note, color=PD_MUTED)], is_ref, muted=True)
    return _pd_card_frame(
        [
            head,
            ui.text(mid, UNSAFE_style={"fontSize": "22px", "fontWeight": 600, **_PD_NUM_STYLE}),
            pd_kv("Spread", spread),
            pd_kv(
                "Book coverage",
                reach,
                PD_UP if reach_ok else PD_WARN,
                "How far from mid the venue's published book reaches (min of bid and ask). "
                "Depth buckets wider than this are lower bounds. Deribit publishes the full "
                "book; OKX caps its feed at 400 levels per side; Hyperliquid's far buckets are aggregated.",
            ),
            ui.text(exact, color=PD_MUTED, UNSAFE_style=_PD_NOTE_STYLE),
        ],
        is_ref,
    )


def _pd_cell(key, text, color=None, align="end", bold=False, bg=None, span=None, small=False):
    style = {
        **_PD_NUM_STYLE,
        "textAlign": "right" if align == "end" else "left",
        "padding": "5px 10px",
        "whiteSpace": "nowrap",
        "borderBottom": "1px solid rgba(128,128,128,0.18)",
        "overflow": "hidden",
        "textOverflow": "ellipsis",
    }
    if bold:
        style["fontWeight"] = 600
    if bg:
        style["background"] = bg
    if small:
        style["fontSize"] = "12px"
    return ui.view(
        ui.text(text, color=color),
        key=key,
        UNSAFE_style=style,
        grid_column=f"span {span}" if span else None,
    )


@ui.component(memo=True)
def pd_matrix(header, rows, lead_cols):
    """Comparison grid. header: tuple of labels; rows: tuple of (row_key, kind, cells), kind in
    'ref' / 'other' / 'nodata' / 'cmp', cells a tuple of (text, color, bold, span). The first
    `lead_cols` columns are left aligned. Keys are stable per venue and column, so a tick
    only rewrites the text of the cells that changed."""
    n = len(header)
    cells = [
        _pd_cell(f"h{i}", h, PD_MUTED, align="start" if i < lead_cols else "end", bold=True)
        for i, h in enumerate(header)
    ]
    for row_key, kind, row_cells in rows:
        bg = {"ref": PD_REF_BG, "cmp": PD_CMP_BG}.get(kind)
        for i, (text, color, bold, span) in enumerate(row_cells):
            cells.append(
                _pd_cell(
                    f"{row_key}-{i}",
                    text,
                    color,
                    align="start" if i < lead_cols or span else "end",
                    bold=bold,
                    bg=bg,
                    span=span,
                    small=kind == "nodata" and i > 0,
                )
            )
    lead = ["minmax(190px, 1.6fr)"] + ["minmax(60px, 0.6fr)"] * (lead_cols - 1)
    cols = " ".join(lead + [f"repeat({n - lead_cols}, minmax(76px, 1fr))"])
    return ui.view(
        ui.grid(*cells, columns=cols, gap="size-0", width="100%"),
        overflow="auto",
        width="100%",
        border_width="thin",
        border_color="gray-300",
        border_radius="medium",
    )


def _pd_venue_label(row, status):
    # Venue name and status as one text: a badge per row would add DOM churn on every tick
    color = PD_UP if status == "LIVE" else (PD_WARN if status == "STALE" else PD_MUTED)
    return (f"{row['Series']}  ·  {status}", color, True, None)


_PD_BLANK = ("", None, False, None)


def pd_depth_matrix_rows(rows, now_ns, side, buckets):
    max_bucket = max(pd_lib.BUCKETS_BPS)
    out, ref, others = [], None, []
    for r in rows:
        status = _pd_status(r, "timestamp", now_ns)
        is_ref = r["exchange"] == PD_REF_EXCHANGE
        key = f"{r['exchange']}:{r['symbol']}"
        if status == "NO DATA":
            note = r.get("Note") or "no rows yet; lights up when data arrives"
            span_cell = (note, PD_MUTED, False, len(buckets) + 2)
            out.append((key, "nodata", (_pd_venue_label(r, status), span_cell)))
            continue
        reach = _pd_reach(r, side)
        reach_color = PD_WARN if pd_lib.is_lower_bound(reach, max_bucket) else None
        cells = [
            _pd_venue_label(r, status),
            (pd_lib.fmt_bps(_pd_num(r.get("spread_bps")), decimals=3), None, False, None),
            (_pd_fmt_reach(reach), reach_color, False, None),
        ]
        vals = {}
        for b in buckets:
            v, lb = _pd_depth(r, side, b)
            vals[b] = (v, lb)
            text = pd_lib.fmt_usd(v)
            text = PD_LB + text if lb and v is not None else text
            cells.append((text, PD_WARN if lb else None, False, None))
        out.append((key, "ref" if is_ref else "other", tuple(cells)))
        if is_ref and ref is None:
            ref = vals
        elif not is_ref:
            others.append(vals)
    # Deribit's multiple of the deepest other venue, with the inequality the lower bounds allow
    cmp_cells = [("Deribit ÷ deepest other", None, True, None), _PD_BLANK, _PD_BLANK]
    for b in buckets:
        d = ref.get(b) if ref else None
        cand = [o[b] for o in others if o.get(b) and o[b][0] is not None]
        if not d or d[0] is None or not cand:
            cmp_cells.append(("-", PD_MUTED, False, None))
            continue
        o_val, o_lb = max(cand, key=lambda vl: vl[0])
        d_val, d_lb = d
        if d_lb and o_lb:
            cmp_cells.append(("both capped", PD_MUTED, False, None))
            continue
        prefix = PD_LB if d_lb else ("≤ " if o_lb else "")
        color = PD_UP if d_val >= o_val else PD_DOWN
        cmp_cells.append((prefix + pd_lib.ratio_text(d_val, o_val), color, True, None))
    out.append(("cmp", "cmp", tuple(cmp_cells)))
    header = ("Venue", "Spread", "Book reach") + tuple(f"±{b} bp" for b in buckets)
    return header, tuple(out)


def pd_slippage_matrix_rows(rows, now_ns):
    keys = list(pd_lib.NOTIONALS)
    out, ref, others = [], {}, {"buy": [], "sell": []}
    for r in rows:
        status = _pd_status(r, "timestamp", now_ns)
        is_ref = r["exchange"] == PD_REF_EXCHANGE
        key = f"{r['exchange']}:{r['symbol']}"
        if status == "NO DATA":
            span_cell = (r.get("Note") or "no rows yet", PD_MUTED, False, len(keys) + 1)
            out.append((key, "nodata", (_pd_venue_label(r, status), span_cell)))
            continue
        for direction in ("buy", "sell"):
            first = _pd_venue_label(r, status) if direction == "buy" else _PD_BLANK
            side_color = PD_UP if direction == "buy" else PD_DOWN
            cells = [first, (direction.capitalize(), side_color, False, None)]
            vals = {}
            for k in keys:
                v = _pd_num(r.get(pd_lib.slip_col(direction, k)))
                vals[k] = v
                if v is None:
                    cells.append(("> book", PD_WARN, False, None))
                else:
                    cells.append((pd_lib.fmt_bps(v), None, False, None))
            out.append((f"{key}:{direction}", "ref" if is_ref else "other", tuple(cells)))
            if is_ref and direction not in ref:
                ref[direction] = vals
            elif not is_ref:
                others[direction].append(vals)
    for direction in ("buy", "sell"):
        cells = [
            ("Deribit − cheapest other", None, True, None),
            (direction.capitalize(), PD_MUTED, False, None),
        ]
        for k in keys:
            d = ref.get(direction, {}).get(k)
            cand = [o[k] for o in others[direction] if o.get(k) is not None]
            if d is None and not cand:
                cells.append(("-", PD_MUTED, False, None))
            elif d is None:
                cells.append(("Deribit > book", PD_DOWN, False, None))
            elif not cand:
                text = "only Deribit fills" if others[direction] else "-"
                cells.append((text, PD_UP, False, None))
            else:
                diff = d - min(cand)
                # within display precision (0.01 bp) it is a tie: no colour
                color = PD_UP if diff <= -0.005 else (PD_DOWN if diff >= 0.005 else None)
                cells.append((pd_lib.fmt_bps(diff, signed=True), color, True, None))
        out.append((f"cmp-{direction}", "cmp", tuple(cells)))
    header = ("Venue", "Side") + tuple(pd_lib.NOTIONAL_LABELS[k] for k in keys)
    return header, tuple(out)


def _pd_chart_box(chart, key, height="size-5000", min_width="size-6000"):
    return ui.view(chart, flex_grow=1, min_width=min_width, height=height, key=key)


# =============================================================================
# Section components (each subscribes to its own small table; only the visible tab renders)
# =============================================================================


@ui.component
def pd_strip(asset, ctype, window):
    book_rows = _pd_rows(ui.use_table_data(pd_family_book[(asset, ctype)]))
    market_rows = _pd_rows(ui.use_table_data(pd_family_market[(asset, ctype, window)]))
    spot_rows = _pd_rows(ui.use_table_data(pd_spot_ref))
    now_ns = time.time_ns()
    statuses = {(r["exchange"], r["symbol"]): _pd_status(r, "timestamp", now_ns) for r in book_rows}
    n_live = sum(1 for s in statuses.values() if s == "LIVE")
    ref_row = next((r for r in book_rows if r["exchange"] == PD_REF_EXCHANGE), None)
    others = [
        r
        for r in book_rows
        if r["exchange"] != PD_REF_EXCHANGE and statuses[(r["exchange"], r["symbol"])] != "NO DATA"
    ]

    def best(fn, lowest):
        vals = [(r["Series"], fn(r)) for r in others]
        vals = [(s, v) for s, v in vals if v is not None]
        return (min if lowest else max)(vals, key=lambda sv: sv[1]) if vals else (None, None)

    def ref(fn):
        return fn(ref_row) if ref_row is not None else None

    def spread(r):
        return _pd_num(r.get("spread_bps"))

    def depth10(r):
        return _pd_depth(r, "both", 10)[0]

    def buy1m(r):
        return _pd_num(r.get(pd_lib.slip_col("buy", "1m")))

    def vs(d, o, lower_is_better):
        if d is None or o is None or abs(d - o) <= 0.01 * abs(o):
            return None  # missing, or a tie within 1%: neutral colour
        return PD_UP if (d <= o if lower_is_better else d >= o) else PD_DOWN

    d_sp, (o_sp_s, o_sp) = ref(spread), best(spread, True)
    d_dp, (o_dp_s, o_dp) = ref(depth10), best(depth10, False)
    d_lb = ref_row is not None and _pd_depth(ref_row, "both", 10)[1]
    o_lb = any(r["Series"] == o_dp_s and _pd_depth(r, "both", 10)[1] for r in others)
    ratio = pd_lib.ratio_text(d_dp, o_dp)
    if d_lb and o_lb:
        ratio = "both capped vs"
    elif d_lb:
        ratio = PD_LB + ratio
    elif o_lb:
        ratio = "≤ " + ratio
    d_buy, (o_buy_s, o_buy) = ref(buy1m), best(buy1m, True)
    total = sum(_pd_num(r.get("usd_vol")) or 0 for r in market_rows)
    ref_usd = sum(
        _pd_num(r.get("usd_vol")) or 0 for r in market_rows if r["exchange"] == PD_REF_EXCHANGE
    )
    spot = next((_pd_num(r["SpotRef"]) for r in spot_rows if r["Asset"] == asset), None)
    o_buy_text = pd_lib.fmt_bps(o_buy) if o_buy is not None else "> book"

    return ui.flex(
        pd_stat(
            "Deribit spread",
            pd_lib.fmt_bps(d_sp, decimals=3),
            f"best other {pd_lib.fmt_bps(o_sp, decimals=3)} ({o_sp_s or '-'})",
            vs(d_sp, o_sp, True),
            key="s1",
        ),
        pd_stat(
            "Deribit depth ±10 bp",
            (PD_LB if d_lb else "") + pd_lib.fmt_usd(d_dp),
            f"{ratio} deepest other ({o_dp_s or '-'})",
            vs(d_dp, o_dp, False),
            key="s2",
        ),
        pd_stat(
            "Deribit $1M buy cost",
            pd_lib.fmt_bps(d_buy) if d_buy is not None else "> book",
            f"cheapest other {o_buy_text} ({o_buy_s or '-'})",
            vs(d_buy, o_buy, True),
            key="s3",
        ),
        pd_stat(
            f"Deribit share of volume ({window})",
            f"{ref_usd / total:.0%}" if total > 0 else "-",
            f"of {pd_lib.fmt_usd(total)} across live venues",
            None,
            key="s4",
        ),
        pd_stat(
            "Venues live",
            f"{n_live} / {len(book_rows)}",
            "others: no data / region blocked" if n_live < len(book_rows) else "all streaming",
            PD_UP if book_rows and n_live == len(book_rows) else PD_WARN,
            key="s5",
        ),
        pd_stat(
            f"{asset} spot ref",
            pd_lib.fmt_price(spot, asset),
            "median of 3 spot venues",
            None,
            key="s6",
        ),
        gap="size-150",
        wrap=True,
    )


@ui.component
def pd_compare_tab(asset, ctype):
    side, set_side = ui.use_state("both")
    coverage, set_coverage = ui.use_state("all buckets")
    hist, set_hist = ui.use_state("10 bp")
    rows = _pd_rows(ui.use_table_data(pd_family_book[(asset, ctype)]))
    now_ns = time.time_ns()

    live = [r for r in rows if _pd_status(r, "timestamp", now_ns) != "NO DATA"]
    if coverage == "common coverage":
        buckets = pd_lib.common_coverage([_pd_reach(r, side) for r in live])
    else:
        buckets = list(pd_lib.BUCKETS_BPS)

    tiles = []
    for r in rows:
        status = _pd_status(r, "timestamp", now_ns)
        reach = _pd_reach(r, "both")
        exact_to = max(
            [b for b in pd_lib.BUCKETS_BPS if not pd_lib.is_lower_bound(reach, b)], default=None
        )
        tiles.append(
            pd_venue_tile(
                r["Series"],
                r["symbol"],
                status,
                r["exchange"] == PD_REF_EXCHANGE,
                r.get("Note") or "no rows yet; lights up when data arrives",
                pd_lib.fmt_price(_pd_num(r.get("mid")), asset),
                pd_lib.fmt_bps(_pd_num(r.get("spread_bps")), decimals=3),
                f"±{_pd_fmt_reach(reach)}",
                exact_to == max(pd_lib.BUCKETS_BPS),
                f"exact to ±{exact_to} bp" if exact_to else "no bucket fully covered",
                key=f"{r['exchange']}:{r['symbol']}",
            )
        )

    if buckets:
        d_header, d_rows = pd_depth_matrix_rows(rows, now_ns, side, buckets)
        depth_block = pd_matrix(d_header, d_rows, 1, key="depth")
    else:
        depth_block = ui.inline_alert(
            ui.heading("No common coverage"),
            ui.content("No depth bucket is fully covered by every live venue right now."),
            variant="notice",
        )
    s_header, s_rows = pd_slippage_matrix_rows(rows, now_ns)

    bucket_chart = pd_cached(
        ("bucket", asset, ctype, side), lambda: pd_bucket_chart(asset, ctype, side)
    )
    slip_chart = pd_cached(("slip", asset, ctype), lambda: pd_slippage_curve_chart(asset, ctype))
    hist_metric = "spread" if hist == "spread" else int(hist.split()[0])
    hist_chart = pd_cached(
        ("hist", asset, ctype, hist_metric),
        lambda: pd_history_chart(asset, ctype, hist_metric),
    )
    hist_block = _pd_chart_box(hist_chart, key="hist", height="size-4600")

    return ui.flex(
        ui.flex(*tiles, gap="size-150", wrap=True, width="100%"),
        ui.flex(
            ui.heading("Resting depth within N bp of mid (USD)", level=3, margin="size-0"),
            ui.flex(
                pd_segmented("Book side", ("both", "bid", "ask"), side, set_side),
                pd_segmented("Buckets", ("all buckets", "common coverage"), coverage, set_coverage),
                gap="size-300",
                wrap=True,
            ),
            justify_content="space-between",
            align_items="end",
            wrap=True,
        ),
        depth_block,
        ui.text(
            "≥ amber = lower bound: the bucket is wider than the venue's published book "
            "(OKX caps its public feed at 400 levels per side; Hyperliquid's far buckets are aggregated). 'Common "
            "coverage' keeps only the buckets every live venue covers completely, for an "
            "apples-to-apples comparison. Bottom row: Deribit's multiple of the deepest other "
            "venue (≤ / ≥ when one side is a lower bound).",
            color=PD_MUTED,
            UNSAFE_style=_PD_NOTE_STYLE,
        ),
        ui.heading("Market order cost vs mid (bp) by notional", level=3, margin="size-0"),
        pd_matrix(s_header, s_rows, 2, key="slip"),
        ui.text(
            "Cost = average fill price vs mid in bp, positive = paid; it includes half the "
            "spread. '> book' = the published book cannot fill that size (not infinite). "
            "Bottom rows: Deribit minus the cheapest other venue (negative / green = Deribit "
            "cheaper).",
            color=PD_MUTED,
            UNSAFE_style=_PD_NOTE_STYLE,
        ),
        ui.flex(
            _pd_chart_box(bucket_chart, key="bucket"),
            _pd_chart_box(slip_chart, key="slipc"),
            gap="size-150",
            wrap=True,
        ),
        ui.flex(
            ui.heading("History", level=3, margin="size-0"),
            pd_segmented(
                "Metric",
                ("spread",) + tuple(f"{b} bp" for b in pd_lib.BUCKETS_BPS),
                hist,
                set_hist,
            ),
            justify_content="space-between",
            align_items="end",
            wrap=True,
        ),
        hist_block,
        direction="column",
        gap="size-150",
        width="100%",
    )


@ui.component(memo=True)
def pd_price_card(series, symbol, status, is_ref, note, values):
    """values: (last, chg, chg_color, mid_spread, prem, prem_color, vs, vs_color, vol, share)"""
    head = _pd_card_head(series, symbol, status)
    if status == "NO DATA":
        return _pd_card_frame([head, ui.text(note, color=PD_MUTED)], is_ref, muted=True)
    last, chg, chg_color, mid_spread, prem, prem_color, vs, vs_color, vol, share = values
    big = {"fontSize": "22px", "fontWeight": 600, **_PD_NUM_STYLE}
    return _pd_card_frame(
        [
            head,
            ui.flex(
                ui.text(last, UNSAFE_style=big),
                ui.text(chg, color=chg_color),
                align_items="baseline",
                gap="size-150",
            ),
            pd_kv("Mid | spread", mid_spread),
            pd_kv(
                "vs spot",
                prem,
                prem_color,
                "Mid (or last trade) vs the median of the latest Coinbase / Kraken / Bitstamp "
                "spot trades. Linear perps settle in USDT/USDC, so their premium also contains "
                "the stablecoin's own USD basis.",
            ),
            pd_kv("vs venues", vs, vs_color, "Mid vs the median mid of the family's live venues."),
            pd_kv("Volume", vol),
            pd_kv("Buy share (aggressor)", share),
        ],
        is_ref,
    )


def _pd_market_table(view, asset, window):
    signed = [
        fmt
        for c in ("ChgBps", "PremSpotBps", "VsFamBps")
        for fmt in (
            ui.TableFormat(cols=[c], if_=f"{c} > 0", color="positive"),
            ui.TableFormat(cols=[c], if_=f"{c} < 0", color="negative"),
        )
    ]
    return ui.view(
        ui.table(
            view,
            format_=[
                ui.TableFormat(cols=["LastPx", "Mid"], value="#,##0.00"),
                ui.TableFormat(cols=["ChgBps"], value="+0.0;-0.0"),
                ui.TableFormat(cols=["SpreadBps"], value="0.000"),
                ui.TableFormat(cols=["PremSpotBps", "VsFamBps"], value="+0.00;-0.00"),
                ui.TableFormat(cols=["UsdVol", "BuyUsd", "SellUsd"], value="$#,##0"),
                ui.TableFormat(cols=["BaseVol"], value="#,##0.000"),
                *signed,
                ui.TableFormat(cols=["UsdVol"], mode=ui.TableDatabar(color="accent")),
                ui.TableFormat(cols=["BuyUsd"], color="positive"),
                ui.TableFormat(cols=["SellUsd"], color="negative"),
                ui.TableFormat(if_="isNull(LastPx) && isNull(Mid)", color="gray-600"),
            ],
            column_display_names={
                "LastPx": "Last",
                "ChgBps": f"Chg {window} (bp)",
                "SpreadBps": "Spread (bp)",
                "PremSpotBps": "vs spot (bp)",
                "VsFamBps": "vs venues (bp)",
                "UsdVol": f"Notional {window} ($)",
                "BaseVol": f"Size {window} ({asset})",
                "BuyUsd": "Buy ($)",
                "SellUsd": "Sell ($)",
            },
            frozen_columns=["Series"],
            density="compact",
        ),
        height="size-2400",
        key=f"mt-{window}",
    )


@ui.component
def pd_prices_tab(asset, ctype, window, set_window):
    rows = _pd_rows(ui.use_table_data(pd_family_market[(asset, ctype, window)]))
    now_ns = time.time_ns()
    cards = []
    for r in rows:
        # last_bucket is the start of the newest 10 s bucket with a trade
        trade_age = _pd_age_s(r.get("last_bucket"), now_ns)
        book_age = _pd_age_s(r.get("BookTs"), now_ns)
        stale_after = _pd_num(r.get("StaleAfter")) or PD_STALE_SECONDS
        status = pd_lib.venue_status(book_age, trade_age, stale_after)
        chg, prem, vs_fam = (_pd_num(r.get(c)) for c in ("ChgBps", "PremSpotBps", "VsFamBps"))
        share = _pd_num(r.get("BuyShare"))
        values = (
            pd_lib.fmt_price(_pd_num(r.get("last_px")), asset),
            f"{pd_lib.fmt_bps(chg, signed=True, decimals=1)} ({window})",
            _pd_signed_color(chg, 0.05),
            f"{pd_lib.fmt_price(_pd_num(r.get('Mid')), asset)} | "
            f"{pd_lib.fmt_bps(_pd_num(r.get('SpreadBps')), decimals=3)}",
            pd_lib.fmt_bps(prem, signed=True),
            _pd_signed_color(prem),
            pd_lib.fmt_bps(vs_fam, signed=True),
            _pd_signed_color(vs_fam, 0.5),
            f"{pd_lib.fmt_usd(_pd_num(r.get('usd_vol')))} | "
            f"{pd_lib.fmt_base(_pd_num(r.get('base_vol')), asset)} ({window})",
            "-" if share is None else f"{share:.0%}",
        )
        cards.append(
            pd_price_card(
                r["Series"],
                r["symbol"],
                status,
                r["exchange"] == PD_REF_EXCHANGE,
                r.get("Note") or "no rows yet; lights up when data arrives",
                values,
                key=f"{r['exchange']}:{r['symbol']}",
            )
        )
    price_chart = pd_cached(("price", asset, ctype), lambda: pd_price_chart(asset, ctype))
    premium_chart = pd_cached(("prem", asset, ctype), lambda: pd_premium_chart(asset, ctype))
    market_view = pd_cached(
        ("mview", asset, ctype, window), lambda: pd_market_view(asset, ctype, window)
    )
    return ui.flex(
        pd_segmented("Window", tuple(PD_WINDOWS), window, set_window),
        ui.flex(*cards, gap="size-150", wrap=True, width="100%"),
        ui.flex(
            _pd_chart_box(price_chart, key="price"),
            _pd_chart_box(premium_chart, key="prem"),
            gap="size-150",
            wrap=True,
        ),
        _pd_market_table(market_view, asset, window),
        direction="column",
        gap="size-200",
        width="100%",
    )


@ui.component
def pd_volume_tab(asset, ctype, window, set_window):
    volume_chart = pd_cached(("vol", asset, ctype), lambda: pd_volume_chart(asset, ctype))
    flow_chart = pd_cached(
        ("flow", asset, ctype, window), lambda: pd_flow_chart(asset, ctype, window)
    )
    market_view = pd_cached(
        ("mview", asset, ctype, window), lambda: pd_market_view(asset, ctype, window)
    )
    return ui.flex(
        pd_segmented("Window", tuple(PD_WINDOWS), window, set_window),
        ui.flex(
            _pd_chart_box(volume_chart, key="vol"),
            _pd_chart_box(flow_chart, key="flow", min_width="size-5000"),
            gap="size-150",
            wrap=True,
        ),
        _pd_market_table(market_view, asset, window),
        direction="column",
        gap="size-200",
        width="100%",
    )


@ui.component
def pd_header(asset, ctype, set_asset, set_ctype):
    family = "inverse (coin-margined, -USD-PERP)" if ctype == "inverse" else "linear (USDT / USDC)"
    source = (
        "perps_liquidity (full published book, ~1 row/s per instrument) and 10 s trade views, "
        "aggregated in QuestDB."
    )
    return ui.flex(
        ui.flex(
            ui.heading(
                "Perps liquidity — Deribit vs OKX · Hyperliquid · Binance · "
                f"Bybit: {asset} {family}",
                level=2,
                margin="size-0",
            ),
            ui.text(
                "Spread, resting depth and market-order cost per symbol, live from QuestDB. "
                f"USD notional (USDT/USDC treated as USD). Source: {source}",
                color=PD_MUTED,
            ),
            direction="column",
            gap="size-50",
        ),
        ui.flex(
            pd_segmented("Asset", tuple(pd_lib.ASSETS), asset, set_asset),
            pd_segmented("Contract", tuple(pd_lib.CONTRACT_TYPES), ctype, set_ctype),
            gap="size-300",
        ),
        justify_content="space-between",
        align_items="end",
        wrap=True,
        gap="size-200",
    )


_PD_TABS = {"compare": "Deribit vs others", "prices": "Prices & basis", "volume": "Volume & flow"}


@ui.component
def pd_app():
    asset, set_asset = ui.use_state("BTC")
    ctype, set_ctype = ui.use_state("linear")
    window, set_window = ui.use_state("5m")
    tab, set_tab = ui.use_state("compare")

    def tab_content(k):
        # Only the selected tab's component exists; the others are empty placeholders
        if k != tab:
            return ui.view()
        if k == "compare":
            return pd_compare_tab(asset, ctype, key=f"c-{asset}-{ctype}")
        if k == "prices":
            return pd_prices_tab(
                asset, ctype, window, set_window, key=f"p-{asset}-{ctype}-{window}"
            )
        return pd_volume_tab(asset, ctype, window, set_window, key=f"v-{asset}-{ctype}-{window}")

    return ui.flex(
        pd_header(asset, ctype, set_asset, set_ctype, key="hdr"),
        # Keys carry the selection: use_table_data keeps its first table for the life of a
        # component, so a new selection must mount a new component
        pd_strip(asset, ctype, window, key=f"strip-{asset}-{ctype}-{window}"),
        ui.tabs(
            *[ui.tab(tab_content(k), title=title, key=k) for k, title in _PD_TABS.items()],
            aria_label="Perps dashboard views",
            selected_key=tab,
            on_selection_change=set_tab,
        ),
        direction="column",
        gap="size-200",
        width="100%",
    )


# Prebuild every chart / view a selection can need with the default sub-options, so switching
# asset, contract or window only swaps cached objects (no table or figure built on a click)
for _a, _c in PD_FAMILIES:
    pd_cached(("bucket", _a, _c, "both"), lambda a=_a, c=_c: pd_bucket_chart(a, c, "both"))
    pd_cached(("slip", _a, _c), lambda a=_a, c=_c: pd_slippage_curve_chart(a, c))
    pd_cached(("hist", _a, _c, 10), lambda a=_a, c=_c: pd_history_chart(a, c, 10))
    pd_cached(("price", _a, _c), lambda a=_a, c=_c: pd_price_chart(a, c))
    pd_cached(("prem", _a, _c), lambda a=_a, c=_c: pd_premium_chart(a, c))
    pd_cached(("vol", _a, _c), lambda a=_a, c=_c: pd_volume_chart(a, c))
    for _w in PD_WINDOWS:
        pd_cached(("flow", _a, _c, _w), lambda a=_a, c=_c, w=_w: pd_flow_chart(a, c, w))
        pd_cached(("mview", _a, _c, _w), lambda a=_a, c=_c, w=_w: pd_market_view(a, c, w))

perps_dashboard = ui.dashboard(ui.panel(pd_app(), title="Perps liquidity"))

print("[OK] 'perps_dashboard' created")
