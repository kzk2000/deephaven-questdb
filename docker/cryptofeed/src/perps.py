"""
BTC / ETH perpetual futures: which venues and symbols to stream, and how to make their trade
and book sizes comparable.

Venues report size in different units:

    linear perp (USDT/USDC margined)   base coin (BTC, ETH)           Binance USDT-M, Bybit, Deribit USDC, Hyperliquid
    linear perp, contracts             contracts x coin per contract    OKX BTC-USDT (0.01 BTC), ETH-USDT (0.1 ETH)
    inverse perp (coin margined)       USD notional                     Deribit, Bybit
    inverse perp, contracts            contracts x USD per contract     OKX (100/10 USD)

`SizeSpec.to_base` turns any of them into a base-coin quantity so that `size` in the trades and
orderbooks tables means the same thing as it does for spot. For inverse contracts that is
USD notional / price, which is exactly the coin amount that changed hands in the trade.

The venue's own number, its unit, the contract multiplier and the dollar notional are stored
next to it on the `trades` row (size_native, size_unit, contract_size, notional_usd).

Nothing here talks to the network at import time. Contract specs are fetched by
`build_specs()`; the HTTP helper is injectable for tests.
"""

import json
import math
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, Optional

import numpy as np

# Coin-per-contract / USD-per-contract values rarely change, but venues do re-list; they are read
# from the venue's public instrument endpoint at start-up.
USER_AGENT = "deephaven-questdb/1.0 (market data research)"


@dataclass(frozen=True)
class SizeSpec:
    """How to convert one venue instrument's native size to a base-coin quantity.

    kind:        "base"    native * multiplier is already base coin
                 "inverse" native * multiplier is USD notional; base = notional / price
    multiplier:  native units -> base coin (base) or -> USD (inverse)
    native_unit: label stored in trades.size_unit: "BASE", "USD" or "CONTRACTS"
    """

    kind: str
    multiplier: float = 1.0
    native_unit: str = "BASE"

    @property
    def contract_type(self) -> str:
        """linear = settled in the quote currency, inverse = coin margined."""
        return "inverse" if self.kind == "inverse" else "linear"

    def notional_usd(self, price: float, native: float) -> float:
        """Dollar notional of a trade. Inverse contracts are denominated in USD directly; for
        linear ones it is base quantity x price (USDT/USDC treated as USD)."""
        value = native * self.multiplier
        return value if self.kind == "inverse" else value * price

    def to_base(self, price: float, native: float) -> float:
        value = native * self.multiplier
        if self.kind == "base":
            return value
        return value / price if price else 0.0

    def to_base_array(self, prices: np.ndarray, natives: np.ndarray) -> np.ndarray:
        """Vectorized `to_base` for one side of an order book."""
        values = np.asarray(natives, dtype=np.float64) * self.multiplier
        if self.kind == "base":
            return values
        prices = np.asarray(prices, dtype=np.float64)
        return np.divide(values, prices, out=np.zeros_like(values), where=prices != 0)


BASE = SizeSpec("base", 1.0, "BASE")
INVERSE_USD = SizeSpec("inverse", 1.0, "USD")


def _get_json(url: str):
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


# --------------------------------------------------------------------------------------------
# per-venue spec builders: normalized symbol -> SizeSpec
# --------------------------------------------------------------------------------------------


def _is_inverse_symbol(symbol: str) -> bool:
    """BTC-USD-PERP is coin margined; BTC-USDT-PERP / BTC-USDC-PERP are linear."""
    return symbol.endswith("-USD-PERP")


def deribit_specs(symbols: Iterable[str], fetch: Callable = _get_json) -> Dict[str, SizeSpec]:
    # Deribit inverse amounts are USD notional; its USDC perps are quoted in base coin.
    return {s: INVERSE_USD if _is_inverse_symbol(s) else BASE for s in symbols}


def bybit_specs(symbols: Iterable[str], fetch: Callable = _get_json) -> Dict[str, SizeSpec]:
    # Bybit v5: linear qty is base coin, inverse qty is USD (1 USD contracts).
    return {s: INVERSE_USD if _is_inverse_symbol(s) else BASE for s in symbols}


def binance_futures_specs(
    symbols: Iterable[str], fetch: Callable = _get_json
) -> Dict[str, SizeSpec]:
    # USDT-margined: quantity is base coin.
    return {s: BASE for s in symbols}


def okx_specs(symbols: Iterable[str], fetch: Callable = _get_json) -> Dict[str, SizeSpec]:
    """OKX reports `sz` in contracts; ctVal / ctType come from the instrument list."""
    data = fetch("https://www.okx.com/api/v5/public/instruments?instType=SWAP")["data"]
    by_inst = {d["instId"]: d for d in data}
    specs = {}
    for symbol in symbols:
        inst = by_inst.get(symbol[: -len("-PERP")] + "-SWAP")
        if inst is None:
            continue
        ct_val = float(inst["ctVal"])
        kind = "inverse" if inst["ctType"] == "inverse" else "base"
        specs[symbol] = SizeSpec(kind, ct_val, "CONTRACTS")
    return specs


def hyperliquid_specs(symbols: Iterable[str], fetch: Callable = _get_json) -> Dict[str, SizeSpec]:
    # Hyperliquid perps are USDC-margined and quoted in base coin, even though cryptofeed names
    # them BTC-USD-PERP (which elsewhere means coin-margined); do not infer from the suffix.
    return {s: BASE for s in symbols}


@dataclass(frozen=True)
class Venue:
    key: str  # name used in PERP_VENUES
    feed_class: str  # attribute of cryptofeed.exchanges
    symbols: tuple  # normalized symbols we want, filtered by what the venue lists
    specs: Callable  # (symbols, fetch) -> {symbol: SizeSpec}
    # cryptofeed symbol -> symbol stored in QuestDB, where cryptofeed's name would mislead
    aliases: Dict[str, str] = field(default_factory=dict)
    # extra aggregated L2 subscriptions (venue "nSigFigs" values, finest first) that reach far
    # beyond the raw book; see DeepBooks
    deep_sig_figs: tuple = ()

    def stored_symbol(self, symbol: str) -> str:
        return self.aliases.get(symbol, symbol)


VENUES: Dict[str, Venue] = {
    "deribit": Venue(
        "deribit",
        "Deribit",
        ("BTC-USD-PERP", "BTC-USDC-PERP", "ETH-USD-PERP", "ETH-USDC-PERP"),
        deribit_specs,
    ),
    "okx": Venue(
        "okx",
        "OKX",
        ("BTC-USD-PERP", "BTC-USDT-PERP", "ETH-USD-PERP", "ETH-USDT-PERP"),
        okx_specs,
    ),
    "binance_futures": Venue(
        "binance_futures",
        "BinanceFutures",
        ("BTC-USDT-PERP", "ETH-USDT-PERP"),  # USDT-M only
        binance_futures_specs,
    ),
    "hyperliquid": Venue(
        "hyperliquid",
        "Hyperliquid",
        ("BTC-USD-PERP", "ETH-USD-PERP"),  # cryptofeed's names for the USDC-margined perps
        hyperliquid_specs,
        aliases={"BTC-USD-PERP": "BTC-USDC-PERP", "ETH-USD-PERP": "ETH-USDC-PERP"},
        # the raw book is 20 levels (~2 bps BTC); 4 / 3 significant figures reach ~25 / ~250 bps
        deep_sig_figs=(4, 3),
    ),
    "bybit": Venue(
        "bybit",
        "Bybit",
        (
            "BTC-USDT-PERP",
            "BTC-USDC-PERP",
            "BTC-USD-PERP",
            "ETH-USDT-PERP",
            "ETH-USDC-PERP",
            "ETH-USD-PERP",
        ),
        bybit_specs,
    ),
}

DEFAULT_VENUES = ("deribit", "okx", "binance_futures", "bybit", "hyperliquid")


def selected_venues(env_value: Optional[str]) -> list:
    """Parse PERP_VENUES ("deribit,okx"); unset means all. Unknown names are an error."""
    if not env_value:
        return list(DEFAULT_VENUES)
    names = [n.strip().lower() for n in env_value.split(",") if n.strip()]
    unknown = [n for n in names if n not in VENUES]
    if unknown:
        raise ValueError(f"unknown PERP_VENUES entries {unknown}; choose from {sorted(VENUES)}")
    return names


def build_specs(
    venue: Venue, available: Iterable[str], fetch: Callable = _get_json
) -> Dict[str, SizeSpec]:
    """Specs for the wanted symbols the venue actually lists."""
    available = set(available)
    wanted = [s for s in venue.symbols if s in available]
    return venue.specs(wanted, fetch) if wanted else {}


# --------------------------------------------------------------------------------------------
# order book liquidity metrics (computed at ingest from the full in-memory book)
# --------------------------------------------------------------------------------------------

# $ notional resting within this many bps of mid, and slippage for market orders of these sizes
DEPTH_BPS = (1, 2, 3, 5, 10, 25, 50, 100)
SLIPPAGE_NOTIONALS = (10_000, 50_000, 100_000, 250_000, 500_000, 1_000_000, 5_000_000)


def notional_label(notional: int) -> str:
    """10_000 -> "10k", 1_000_000 -> "1m", 50 -> "50" (used in column names)."""
    if notional >= 1_000_000:
        return f"{notional // 1_000_000}m"
    if notional >= 1000:
        return f"{notional // 1000}k"
    return str(notional)


# Ordered column layout of the perps_liquidity table (after timestamp / exchange / symbol /
# contract_type). DOUBLE unless listed in LIQUIDITY_LONG_COLUMNS.
LIQUIDITY_LONG_COLUMNS = ("bid_levels", "ask_levels")
LIQUIDITY_DOUBLE_COLUMNS = (
    ("mid", "best_bid", "best_ask", "spread_bps", "bid_reach_bps", "ask_reach_bps")
    + tuple(f"bid_usd_{n}bp" for n in DEPTH_BPS)
    + tuple(f"ask_usd_{n}bp" for n in DEPTH_BPS)
    + tuple(f"buy_slip_bps_{notional_label(n)}" for n in SLIPPAGE_NOTIONALS)
    + tuple(f"sell_slip_bps_{notional_label(n)}" for n in SLIPPAGE_NOTIONALS)
)


def _walk(prices: np.ndarray, base_sizes: np.ndarray, notional: float):
    """Average fill price of a market order for `notional` USD, or None if the book is too thin.

    `prices` run from best outward. Level k is consumed fully (price * size dollars) until the
    order is filled, with a partial fill on the last level.
    """
    level_usd = prices * base_sizes
    cumulative = np.cumsum(level_usd)
    if len(cumulative) == 0 or cumulative[-1] < notional:
        return None
    k = int(np.searchsorted(cumulative, notional))  # first level where cumulative >= notional
    spent_before = cumulative[k - 1] if k > 0 else 0.0
    quantity = float(base_sizes[:k].sum()) + (notional - spent_before) / prices[k]
    return notional / quantity


def bucket_step(price: float, n_sig_figs: int) -> float:
    """Price width of one aggregated level when a venue rounds prices to n significant figures."""
    return 10.0 ** (math.floor(math.log10(price)) - n_sig_figs + 1)


def extend_side(prices, sizes, agg_prices, agg_sizes, step: float, is_bid: bool):
    """Append the part of an aggregated book side that lies beyond a finer book's last level.

    Aggregated levels are labelled with the edge of their bucket farthest from the mid (bids
    round down: bucket [p, p + step); asks round up: bucket (p - step, p]), which is how
    Hyperliquid's nSigFigs books behave. A bucket straddling the finer book's last level
    contributes only what the finer book did not already count, placed at its label price (the
    far end of its range, so depth within N bps is never overstated). Order of the inputs does
    not matter; the result is unsorted.
    """
    prices = np.asarray(prices, dtype=np.float64)
    sizes = np.asarray(sizes, dtype=np.float64)
    agg_prices = np.asarray(agg_prices, dtype=np.float64)
    agg_sizes = np.asarray(agg_sizes, dtype=np.float64)
    if len(prices) == 0 or len(agg_prices) == 0:
        return prices, sizes
    edge = prices.min() if is_bid else prices.max()
    out_p, out_s = [prices], [sizes]
    tol = step * 1e-9
    for p, size in zip(agg_prices, agg_sizes):
        if is_bid:
            if p >= edge - tol:
                continue
            inside = (prices >= p - tol) & (prices < p + step - tol)
        else:
            if p <= edge + tol:
                continue
            inside = (prices > p - step + tol) & (prices <= p + tol)
        remainder = size - sizes[inside].sum()
        if remainder > 0:
            out_p.append(np.array([p]))
            out_s.append(np.array([remainder]))
    return np.concatenate(out_p), np.concatenate(out_s)


class DeepBooks:
    """Latest aggregated books per (symbol, n_sig_figs), shared between feeds.

    One feed per aggregation level writes here; the liquidity callback of the raw book reads
    through `extend`, which appends each coarser book beyond what the finer ones cover. Entries
    older than max_age_seconds are ignored, so a stalled subscription degrades to the raw book.
    """

    def __init__(self, max_age_seconds: float = 30.0, clock: Callable = time.monotonic):
        self._max_age = max_age_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._books: Dict[tuple, tuple] = {}

    def update(self, symbol, n_sig_figs, bid_prices, bid_sizes, ask_prices, ask_sizes):
        with self._lock:
            self._books[(symbol, n_sig_figs)] = (
                self._clock(),
                (bid_prices, bid_sizes, ask_prices, ask_sizes),
            )

    def extend(self, symbol, bid_prices, bid_sizes, ask_prices, ask_sizes):
        """The raw book with every fresh aggregated book appended beyond its edge (finest first)."""
        with self._lock:
            levels = sorted((n, entry) for (sym, n), entry in self._books.items() if sym == symbol)
            now = self._clock()
        for n_sig_figs, (stamp, (bp, bs, ap, a_s)) in sorted(levels, reverse=True):
            if now - stamp > self._max_age:
                continue
            mid_price = (np.max(bid_prices) + np.min(ask_prices)) / 2.0
            step = bucket_step(mid_price, n_sig_figs)
            bid_prices, bid_sizes = extend_side(bid_prices, bid_sizes, bp, bs, step, is_bid=True)
            ask_prices, ask_sizes = extend_side(ask_prices, ask_sizes, ap, a_s, step, is_bid=False)
        return bid_prices, bid_sizes, ask_prices, ask_sizes


def compute_liquidity(
    bid_prices, bid_sizes, ask_prices, ask_sizes, bps=DEPTH_BPS, notionals=SLIPPAGE_NOTIONALS
):
    """Spread, depth within N bps and slippage for a book, or None for an empty/crossed book.

    Sizes are base coin (so price * size is USD for linear and inverse contracts alike). Levels
    may arrive in any order; they are sorted best-first here. Depth buckets are cumulative and
    lower bounds when `*_reach_bps` is smaller than the bucket (the feed published too little
    book); a slippage of None means the visible book cannot fill that notional.
    """
    bid_prices = np.asarray(bid_prices, dtype=np.float64)
    bid_sizes = np.asarray(bid_sizes, dtype=np.float64)
    ask_prices = np.asarray(ask_prices, dtype=np.float64)
    ask_sizes = np.asarray(ask_sizes, dtype=np.float64)
    bid_live, ask_live = bid_sizes > 0, ask_sizes > 0
    bid_prices, bid_sizes = bid_prices[bid_live], bid_sizes[bid_live]
    ask_prices, ask_sizes = ask_prices[ask_live], ask_sizes[ask_live]
    if len(bid_prices) == 0 or len(ask_prices) == 0:
        return None

    order = np.argsort(-bid_prices)
    bid_prices, bid_sizes = bid_prices[order], bid_sizes[order]
    order = np.argsort(ask_prices)
    ask_prices, ask_sizes = ask_prices[order], ask_sizes[order]

    best_bid, best_ask = float(bid_prices[0]), float(ask_prices[0])
    if best_bid >= best_ask or best_bid <= 0:
        return None  # crossed or broken book: not a meaningful snapshot
    mid = (best_bid + best_ask) / 2.0

    bid_distance = (mid - bid_prices) / mid * 1e4  # bps, ascending along the book
    ask_distance = (ask_prices - mid) / mid * 1e4
    bid_cumulative = np.cumsum(bid_prices * bid_sizes)
    ask_cumulative = np.cumsum(ask_prices * ask_sizes)

    out = {
        "mid": mid,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread_bps": (best_ask - best_bid) / mid * 1e4,
        "bid_levels": int(len(bid_prices)),
        "ask_levels": int(len(ask_prices)),
        "bid_reach_bps": float(bid_distance[-1]),
        "ask_reach_bps": float(ask_distance[-1]),
    }
    for n in bps:
        kb = int(np.searchsorted(bid_distance, n, side="right"))
        ka = int(np.searchsorted(ask_distance, n, side="right"))
        out[f"bid_usd_{n}bp"] = float(bid_cumulative[kb - 1]) if kb else 0.0
        out[f"ask_usd_{n}bp"] = float(ask_cumulative[ka - 1]) if ka else 0.0
    for n in notionals:
        label = notional_label(n)
        buy = _walk(ask_prices, ask_sizes, n)
        sell = _walk(bid_prices, bid_sizes, n)
        # plain floats: the QuestDB SDK rejects numpy scalar types
        out[f"buy_slip_bps_{label}"] = None if buy is None else float((buy - mid) / mid * 1e4)
        out[f"sell_slip_bps_{label}"] = None if sell is None else float((mid - sell) / mid * 1e4)
    return out
