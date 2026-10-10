"""
BTC / ETH perpetual futures: which venues and symbols to stream, and how to make their trade
and book sizes comparable.

Venues report size in different units:

    linear perp (USDT/USDC margined)   base coin (BTC, ETH)           Binance, Bybit, Deribit USDC
    linear perp, contracts             contracts x coin per contract    OKX BTC-USDT (0.01 BTC), ETH-USDT (0.1 ETH)
    inverse perp (coin margined)       USD notional                     Deribit, Bybit
    inverse perp, contracts            contracts x USD per contract     OKX (100/10 USD), Binance delivery (100/10 USD)

`SizeSpec.to_base` turns any of them into a base-coin quantity so that `size` in the trades and
orderbooks tables means the same thing as it does for spot. For inverse contracts that is
USD notional / price, which is exactly the coin amount that changed hands in the trade.

The venue's own number, its unit, the contract multiplier and the dollar notional are stored
next to it on the `trades` row (size_native, size_unit, contract_size, notional_usd).

Nothing here talks to the network at import time. Contract specs are fetched by
`build_specs()`; the HTTP helper is injectable for tests.
"""

import json
import urllib.request
from dataclasses import dataclass
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


def binance_delivery_specs(
    symbols: Iterable[str], fetch: Callable = _get_json
) -> Dict[str, SizeSpec]:
    """COIN-M quantity is a contract count; contractSize (USD per contract) is in exchangeInfo."""
    info = fetch("https://dapi.binance.com/dapi/v1/exchangeInfo")["symbols"]
    by_symbol = {d["symbol"]: d for d in info}
    specs = {}
    for symbol in symbols:
        base, quote, _ = symbol.split("-")  # BTC-USD-PERP -> BTCUSD_PERP
        entry = by_symbol.get(f"{base}{quote}_PERP")
        if entry is None:
            continue
        specs[symbol] = SizeSpec("inverse", float(entry["contractSize"]), "CONTRACTS")
    return specs


@dataclass(frozen=True)
class Venue:
    key: str  # name used in PERP_VENUES
    feed_class: str  # attribute of cryptofeed.exchanges
    symbols: tuple  # normalized symbols we want, filtered by what the venue lists
    specs: Callable  # (symbols, fetch) -> {symbol: SizeSpec}


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
        ("BTC-USDT-PERP", "BTC-USDC-PERP", "ETH-USDT-PERP", "ETH-USDC-PERP"),
        binance_futures_specs,
    ),
    "binance_delivery": Venue(
        "binance_delivery",
        "BinanceDelivery",
        ("BTC-USD-PERP", "ETH-USD-PERP"),
        binance_delivery_specs,
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

DEFAULT_VENUES = ("deribit", "okx", "binance_futures", "binance_delivery", "bybit")


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
