"""
Pure-Python (numpy only) math and reference data for the perpetual-futures dashboard.

Kept free of Deephaven imports so it is unit-tested server-free
(docker/cryptofeed/tests/unit/test_perps_dash_lib.py). perps_dashboard.py uses the venue
registry, the perps_liquidity column names, the coverage helpers and the formatting. The book
math (depth_within, walk_book, liquidity_row) re-derives the ingest's perps_liquidity metrics
from a stored book, as an independent reference for cross-checks.

Book conventions (orderbooks table): per side two arrays, prices and sizes in BASE coin (already
normalized for inverse and contract venues), at most 20 levels. Level notional in USD is
price * size for both linear and inverse perps (USDT/USDC treated as USD).

Sign conventions:
    spread_bps   (best_ask - best_bid) / mid * 1e4
    slippage     cost versus mid in bps, positive = worse than mid, for both buy and sell
                 (so it includes the half spread: a $1 market buy on a 1 bp book costs ~0.5 bp)
    truncated    the levels given did not cover the request (depth band wider than the book's
                 reach, or order bigger than the book): the number is a lower bound
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# =============================================================================
# Venues and instruments (data, not layout: the dashboard renders whatever is listed here)
# =============================================================================


@dataclass(frozen=True)
class Venue:
    exchange: str  # value of the `exchange` column
    label: str  # short display name
    symbols: tuple  # perps this venue offers (mirrors docker/cryptofeed/src/perps.py)
    note: str  # shown when the venue has no data, explains the likely reason
    # Seconds without a new row before the venue is STALE. Per venue because cadences differ:
    # Hyperliquid's public book arrives only every ~6 s, the others update many times a second
    stale_after_s: float = 15.0
    # what limits this venue's published book (shown in the dashboard's "Book coverage" tooltip)
    coverage_note: str = ""


VENUES = (
    Venue(
        "DERIBIT",
        "Deribit",
        ("BTC-USD-PERP", "BTC-USDC-PERP", "ETH-USD-PERP", "ETH-USDC-PERP"),
        "",
        coverage_note="Deribit publishes its whole book.",
    ),
    Venue(
        "OKX",
        "OKX",
        ("BTC-USD-PERP", "BTC-USDT-PERP", "ETH-USD-PERP", "ETH-USDT-PERP"),
        "",
        coverage_note="OKX's public feed is capped at 400 levels per side.",
    ),
    Venue(
        "HYPERLIQUID",
        "Hyperliquid",
        ("BTC-USDC-PERP", "ETH-USDC-PERP"),
        "",
        stale_after_s=30.0,
        coverage_note=(
            "Hyperliquid's own book is 20 levels per side; the farther depth comes from "
            "price-aggregated books and is a lower bound."
        ),
    ),
    Venue(
        "BINANCE_FUTURES",
        "Binance",
        ("BTC-USDT-PERP", "ETH-USDT-PERP"),  # USDT-M only
        "no data received (REST is region blocked here; the WebSocket feed is down?)",
        coverage_note=(
            "Binance's WebSocket partial book is 20 levels per side (about 0.3 bp on BTC); a "
            "deeper book needs REST, which is blocked from this host."
        ),
    ),
    Venue(
        "BYBIT",
        "Bybit",
        (
            "BTC-USDT-PERP",
            "BTC-USDC-PERP",
            "BTC-USD-PERP",
            "ETH-USDT-PERP",
            "ETH-USDC-PERP",
            "ETH-USD-PERP",
        ),
        "no data received (REST is region blocked here; the WebSocket feed is down?)",
        coverage_note="Bybit publishes 1000 levels per side.",
    ),
)

ASSETS = ("BTC", "ETH")
CONTRACT_TYPES = ("linear", "inverse")

# Spot venues whose last trade prices form the reference price for basis
SPOT_REFERENCE_EXCHANGES = ("COINBASE", "KRAKEN", "BITSTAMP")


def coverage_note(exchange: str) -> str:
    """Why this venue's published book reaches only so far (tooltip text)."""
    for venue in VENUES:
        if venue.exchange == exchange:
            return venue.coverage_note
    return ""


def parse_symbol(symbol: str) -> tuple[str, str, str]:
    """'BTC-USDT-PERP' -> ('BTC', 'USDT', 'linear'); 'ETH-USD-PERP' -> ('ETH', 'USD', 'inverse').

    USD-quoted perps are coin margined (inverse); USDT / USDC ones are linear.
    """
    parts = symbol.split("-")
    if len(parts) != 3 or parts[2] != "PERP":
        raise ValueError(f"not a perp symbol: {symbol!r}")
    asset, quote = parts[0], parts[1]
    return asset, quote, "inverse" if quote == "USD" else "linear"


def instrument_rows() -> list[dict]:
    """One row per (venue, perp) with display labels, in VENUES order."""
    rows = []
    for order, venue in enumerate(VENUES):
        for symbol in venue.symbols:
            asset, quote, ctype = parse_symbol(symbol)
            # Inverse perps are all USD quoted, so the venue name alone is unambiguous;
            # linear venues can list both USDT and USDC books, so the quote is part of the name
            series = venue.label if ctype == "inverse" else f"{venue.label} {quote}"
            rows.append(
                {
                    "exchange": venue.exchange,
                    "symbol": symbol,
                    "Venue": venue.label,
                    "Series": series,
                    "Asset": asset,
                    "Quote": quote,
                    "Ctype": ctype,
                    "Note": venue.note,
                    "VenueOrder": order,
                    "StaleAfter": venue.stale_after_s,
                }
            )
    return rows


def family_rows(asset: str, ctype: str) -> list[dict]:
    """Instruments of one family (e.g. BTC linear) across every venue."""
    return [r for r in instrument_rows() if r["Asset"] == asset and r["Ctype"] == ctype]


def venue_status(book_age_s, trade_age_s, stale_after_s: float = 15.0) -> str:
    """LIVE when the book (or, without a book, the last trade) is fresh; STALE when data exists
    but is old; NO DATA when nothing was ever received (e.g. a venue whose feed is down)."""
    ages = [a for a in (book_age_s, trade_age_s) if _is_num(a)]
    if not ages:
        return "NO DATA"
    if _is_num(book_age_s):
        return "LIVE" if book_age_s <= stale_after_s else "STALE"
    # No book (only trades): trades on a quiet perp can be minutes apart, so be more lenient
    return "LIVE" if trade_age_s <= 4 * stale_after_s else "STALE"


# =============================================================================
# Order book math (vectorized per book; a book has <= 20 levels per side)
# =============================================================================

BPS = 1e4
EPS_BPS = 1e-6  # tolerance for "level exactly at the band edge"


def _is_num(x) -> bool:
    try:
        return x is not None and bool(np.isfinite(float(x)))
    except (TypeError, ValueError):
        return False


def clean_side(px, sz, is_bid: bool) -> tuple[np.ndarray, np.ndarray]:
    """Drop NULL / NaN / non-positive levels and sort best first (bids descending).

    Deephaven passes NULL arrays as None and NULL doubles as NaN or the DH null sentinel
    (-Double.MAX_VALUE), all of which are removed by the positivity mask.
    """
    if px is None or sz is None:
        return np.empty(0), np.empty(0)
    px = np.asarray(px, dtype=np.float64).ravel()
    sz = np.asarray(sz, dtype=np.float64).ravel()
    n = min(px.size, sz.size)
    px, sz = px[:n], sz[:n]
    ok = np.isfinite(px) & np.isfinite(sz) & (px > 0) & (sz > 0)
    px, sz = px[ok], sz[ok]
    order = np.argsort(-px if is_bid else px, kind="stable")
    return px[order], sz[order]


def mid_spread(bid_px: np.ndarray, ask_px: np.ndarray) -> tuple[float, float]:
    """Mid price and spread in bps from cleaned (best-first) sides; NaN if a side is empty."""
    if bid_px.size == 0 or ask_px.size == 0:
        return np.nan, np.nan
    bid, ask = bid_px[0], ask_px[0]
    mid = 0.5 * (bid + ask)
    return mid, (ask - bid) / mid * BPS


def depth_within(px, sz, mid: float, bps: float, is_bid: bool) -> tuple[float, bool]:
    """USD notional resting within `bps` of mid on one side, and whether it is truncated.

    Truncated (a lower bound) when every stored level is inside the band: the band may extend
    beyond the 20 visible levels, so deeper liquidity inside it is unseen.
    """
    if px.size == 0 or not np.isfinite(mid):
        return np.nan, True
    # Compare distances (same formula and tolerance as depth_curve) so both always agree
    inside = np.abs(px / mid - 1) * BPS <= bps + EPS_BPS
    usd = float(np.sum(px[inside] * sz[inside]))
    return usd, bool(inside.all())


def walk_book(px, sz, notional_usd: float, mid: float, is_bid: bool):
    """Fill a market order of `notional_usd` against one side (sell hits bids, buy lifts asks).

    Returns (avg_price, slippage_bps_vs_mid, filled_usd, truncated). When the visible book is
    smaller than the order, the whole visible book is consumed and truncated=True: the
    slippage then describes only the filled part and the true cost is higher (a lower bound).
    """
    if px.size == 0 or not np.isfinite(mid) or not notional_usd > 0:
        return np.nan, np.nan, 0.0, True
    level_usd = px * sz
    cum = np.cumsum(level_usd)
    if cum[-1] < notional_usd:
        filled_usd = float(cum[-1])
        base = float(np.sum(sz))
        truncated = True
    else:
        # k = first level at which the cumulative notional reaches the order size
        k = int(np.searchsorted(cum, notional_usd, side="left"))
        prev = float(cum[k - 1]) if k > 0 else 0.0
        base = float(np.sum(sz[:k])) + (notional_usd - prev) / px[k]
        filled_usd = float(notional_usd)
        truncated = False
    avg = filled_usd / base
    slip = (1 - avg / mid) * BPS if is_bid else (avg / mid - 1) * BPS
    return avg, slip, filled_usd, truncated


# =============================================================================
# Formatting (one place, so every card and label uses the same units and precision)
# =============================================================================


def price_decimals(asset: str) -> int:
    return 1 if asset == "BTC" else 2


def fmt_price(x, asset: str = "BTC") -> str:
    return f"{x:,.{price_decimals(asset)}f}" if _is_num(x) else "-"


def fmt_usd(x) -> str:
    """$1.23M / $456.7k / $812: compact USD notional."""
    if not _is_num(x):
        return "-"
    a = abs(x)
    sign = "-" if x < 0 else ""
    if a >= 1e9:
        return f"{sign}${a / 1e9:,.2f}B"
    if a >= 1e6:
        return f"{sign}${a / 1e6:,.2f}M"
    if a >= 1e3:
        return f"{sign}${a / 1e3:,.1f}k"
    return f"{sign}${a:,.0f}"


def fmt_bps(x, signed: bool = False, decimals: int = 2) -> str:
    if not _is_num(x):
        return "-"
    return f"{x:+,.{decimals}f} bp" if signed else f"{x:,.{decimals}f} bp"


def fmt_base(x, asset: str) -> str:
    return f"{x:,.2f} {asset}" if _is_num(x) else "-"


# =============================================================================
# perps_liquidity schema (written by the ingest from the full in-memory book, ~1 row/s per
# instrument). liquidity_row() computes the same columns from a stored book, as an
# independent reference for cross-checks.
# =============================================================================

BUCKETS_BPS = (1, 2, 3, 5, 10, 25, 50, 100)
NOTIONALS = {
    "10k": 10_000,
    "50k": 50_000,
    "100k": 100_000,
    "250k": 250_000,
    "500k": 500_000,
    "1m": 1_000_000,
    "5m": 5_000_000,
}
NOTIONAL_LABELS = {k: "$" + k.upper().replace("K", "k") for k in NOTIONALS}  # "$10k", "$1M"


def depth_col(side: str, bps: int) -> str:
    return f"{side}_usd_{bps}bp"


def slip_col(direction: str, key: str) -> str:
    return f"{direction}_slip_bps_{key}"


LIQ_COLUMNS = (
    ("mid", "best_bid", "best_ask", "spread_bps")
    + ("bid_levels", "ask_levels", "bid_reach_bps", "ask_reach_bps")
    + tuple(depth_col("bid", b) for b in BUCKETS_BPS)
    + tuple(depth_col("ask", b) for b in BUCKETS_BPS)
    + tuple(slip_col("buy", k) for k in NOTIONALS)
    + tuple(slip_col("sell", k) for k in NOTIONALS)
)


def liquidity_row(bid_px, bid_sz, ask_px, ask_sz) -> np.ndarray:
    """Values in LIQ_COLUMNS order for one book. NaN = undefined; a slippage is NaN when the
    book cannot fill the order (the table's NULL rule: 'more than the visible book')."""
    bp, bs = clean_side(bid_px, bid_sz, True)
    ap, asz = clean_side(ask_px, ask_sz, False)
    mid, spread = mid_spread(bp, ap)
    ok = np.isfinite(mid)
    out = [
        mid,
        bp[0] if bp.size else np.nan,
        ap[0] if ap.size else np.nan,
        spread,
        float(bp.size),
        float(ap.size),
        (1 - bp[-1] / mid) * BPS if bp.size and ok else np.nan,
        (ap[-1] / mid - 1) * BPS if ap.size and ok else np.nan,
    ]
    out += [depth_within(bp, bs, mid, b, True)[0] for b in BUCKETS_BPS]
    out += [depth_within(ap, asz, mid, b, False)[0] for b in BUCKETS_BPS]
    for px, sz, is_bid in ((ap, asz, False), (bp, bs, True)):
        for n in NOTIONALS.values():
            _, slip, _, truncated = walk_book(px, sz, n, mid, is_bid)
            out.append(np.nan if truncated else slip)
    return np.asarray(out, dtype=np.float64)


def is_lower_bound(reach_bps, bucket_bps) -> bool:
    """A depth bucket wider than the published book's reach is only a lower bound.

    Unknown reach (NULL) counts as a lower bound: we cannot claim the bucket is complete."""
    return not (_is_num(reach_bps) and reach_bps >= bucket_bps - EPS_BPS)


def common_coverage(reaches) -> list[int]:
    """Buckets that every venue's book fully covers (apples-to-apples comparison set).

    `reaches` = one min(bid_reach, ask_reach) per venue with data; empty input -> []."""
    vals = [r for r in reaches if _is_num(r)]
    if not vals:
        return []
    floor = min(vals)
    return [b for b in BUCKETS_BPS if b <= floor + EPS_BPS]


def ratio_text(ref, other) -> str:
    """'2.4x' style multiple of ref over other (for 'Deribit vs best other' rows)."""
    if not (_is_num(ref) and _is_num(other)) or other <= 0:
        return "-"
    r = ref / other
    return f"{r:,.1f}x" if r >= 0.995 else f"{r:,.2f}x"
