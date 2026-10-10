"""
BTC / ETH perpetual futures (linear and inverse) from Binance, Bybit, Deribit, Hyperliquid
and OKX.

Trades and L2 books go to the same `trades` / `orderbooks` tables as the spot feeds.
`exchange` is BINANCE_FUTURES (USDT-M only), BYBIT, DERIBIT, HYPERLIQUID or OKX; `symbol` is
BTC-USD-PERP (inverse, coin margined) or BTC-USDT-PERP / BTC-USDC-PERP (linear).

`size` is normalized to base coin so it is comparable with spot. trades also keeps the venue's
own number (size_native, size_unit), the contract multiplier (contract_size, 1 for spot), the
margin type (contract_type: spot / linear / inverse) and the dollar notional (notional_usd);
see perps.py.

Per second and instrument it also writes `perps_liquidity` (spread, $ depth within 1..100 bps of
mid, slippage for $10k..$5m market orders) computed from the full in-memory book, which
QuestDB turns into the 1-minute view perps_liquidity_1m.

Venues that cannot be reached (Binance and Bybit refuse some regions with HTTP 451/403) are
skipped and re-probed every PERP_REPROBE_SECONDS; the process restarts itself when one becomes
reachable so it is picked up without operator action.

Environment:
    PERP_VENUES            comma list from deribit,okx,binance_futures,bybit,hyperliquid
                           (default: all)
    PERP_REPROBE_SECONDS   seconds between re-probes of skipped venues (default 600, 0 = never)
"""

import contextlib
import logging
import os
import sys
import threading
import time

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import perps
from questdb_writer import QuestDBWriter

ORDERBOOK_DEPTH = 20  # levels per side written to the orderbooks table
BOOK_DEPTH = 0  # keep the whole book in memory (0 = unlimited) for the liquidity metrics
LIQUIDITY_INTERVAL_SECONDS = 1.0  # at most one perps_liquidity row per instrument per second
RETRY_SLEEP_SECONDS = 60


def trade_callback(writer, specs, aliases=None):
    """Write a trade: `size` in base coin, plus native size, contract multiplier and notional."""

    aliases = aliases or {}

    async def on_trade(trade, receipt_timestamp):
        spec = specs.get(trade.symbol)
        if spec is None:
            return
        price = float(trade.price)
        native = float(trade.amount)
        writer.write_trade(
            {
                "exchange": trade.exchange,
                "symbol": aliases.get(trade.symbol, trade.symbol),
                "side": trade.side,
                "price": price,
                "amount": spec.to_base(price, native),
                "size_native": native,
                "size_unit": spec.native_unit,
                "contract_size": spec.multiplier,
                "contract_type": spec.contract_type,
                "notional_usd": spec.notional_usd(price, native),
                "id": trade.id,
                "timestamp": trade.timestamp,
                "receipt_timestamp": receipt_timestamp,
            }
        )

    return on_trade


def book_callback(writer, specs, aliases=None):
    """Write an order book snapshot with level sizes converted to base coin."""

    aliases = aliases or {}

    async def on_book(book, receipt_timestamp):
        spec = specs.get(book.symbol)
        if spec is None:
            return
        try:
            writer.write_orderbook(
                book,
                receipt_timestamp,
                depth=ORDERBOOK_DEPTH,
                size_fn=spec.to_base_array,
                symbol=aliases.get(book.symbol),
            )
        except Exception as e:  # keep the feed alive on a malformed update
            print(f"Error writing {book.exchange} {book.symbol} orderbook: {e}")

    return on_book


@contextlib.contextmanager
def quiet_loggers(*names):
    """Raise the named loggers above ERROR for the duration, then restore them.

    cryptofeed logs a full response body and two tracebacks for every refused request; a
    region-blocked venue is an expected condition here and we report it in one line instead.
    """
    loggers = [logging.getLogger(n) for n in names]
    previous = [lg.level for lg in loggers]
    for lg in loggers:
        lg.setLevel(logging.CRITICAL + 1)
    try:
        yield
    finally:
        for lg, level in zip(loggers, previous):
            lg.setLevel(level)


def _side_arrays(side):
    """(prices, sizes) of one side of a cryptofeed order book as float arrays."""
    items = list(side.items())
    prices = np.fromiter((float(p) for p, _ in items), dtype=np.float64, count=len(items))
    sizes = np.fromiter((float(s) for _, s in items), dtype=np.float64, count=len(items))
    return prices, sizes


def deep_book_callback(deep_books, n_sig_figs):
    """Store an aggregated book (venue nSigFigs subscription) for the liquidity callback."""

    async def on_book(book, receipt_timestamp):
        bid_prices, bid_sizes = _side_arrays(book.book.bids)
        ask_prices, ask_sizes = _side_arrays(book.book.asks)
        deep_books.update(book.symbol, n_sig_figs, bid_prices, bid_sizes, ask_prices, ask_sizes)

    return on_book


def aggregated_feed_class(feed_class, n_sig_figs):
    """A Hyperliquid feed subscribed to `nSigFigs` price-aggregated books.

    The venue serves 20 levels per side whatever the aggregation, so coarser prices reach
    further from the mid (BTC: ~2 bps raw, ~25 bps at 4 figures, ~250 bps at 3). cryptofeed
    offers no way to pass the option, hence the subclass.
    """
    import json

    class Aggregated(feed_class):
        async def subscribe(self, conn):
            self._reset()
            for channel, symbols in conn.subscription.items():
                for symbol in symbols:
                    await conn.write(
                        json.dumps(
                            {
                                "method": "subscribe",
                                "subscription": {
                                    "type": channel,
                                    "coin": symbol,
                                    "nSigFigs": n_sig_figs,
                                },
                            }
                        )
                    )

    Aggregated.__name__ = f"{feed_class.__name__}Agg{n_sig_figs}"
    return Aggregated


def liquidity_callback(
    writer,
    specs,
    interval=LIQUIDITY_INTERVAL_SECONDS,
    clock=time.monotonic,
    table="perps_liquidity",
    aliases=None,
    deep_books=None,
):
    """Throttled spread / depth-within-N-bps / slippage metrics from the full in-memory book.

    Venues publish different amounts of book (Deribit all of it, OKX 400 levels per side), so
    each row also records how far from mid the published book reaches (bid/ask_reach_bps) and
    consumers can tell an exact bucket from a lower bound.
    """
    last_emit = {}
    aliases = aliases or {}

    async def on_book(book, receipt_timestamp):
        spec = specs.get(book.symbol)
        if spec is None:
            return
        key = (book.exchange, book.symbol)
        now = clock()
        if now - last_emit.get(key, float("-inf")) < interval:
            return
        last_emit[key] = now
        try:
            bid_prices, bid_sizes = _side_arrays(book.book.bids)
            ask_prices, ask_sizes = _side_arrays(book.book.asks)
            if deep_books is not None and len(bid_prices) and len(ask_prices):
                bid_prices, bid_sizes, ask_prices, ask_sizes = deep_books.extend(
                    book.symbol, bid_prices, bid_sizes, ask_prices, ask_sizes
                )
            metrics = perps.compute_liquidity(
                bid_prices,
                spec.to_base_array(bid_prices, bid_sizes),
                ask_prices,
                spec.to_base_array(ask_prices, ask_sizes),
            )
            if metrics is None:
                return
            writer.write_row(
                table,
                symbols={
                    "exchange": book.exchange,
                    "symbol": aliases.get(book.symbol, book.symbol),
                    "contract_type": spec.contract_type,
                },
                columns=metrics,
                timestamp=book.timestamp or receipt_timestamp,
            )
        except Exception as e:  # never let a metrics error stop the feed
            print(f"Error computing {book.exchange} {book.symbol} liquidity: {e}")

    return on_book


def probe(venue):
    """(feed class, listed symbols, error) for a venue; error is None when reachable."""
    import cryptofeed.exchanges as cryptofeed_exchanges  # lazy: keeps this module importable

    if venue.feed_factory:
        import importlib

        module, function = venue.feed_factory.split(":")
        feed_class = getattr(importlib.import_module(module), function)()
    else:
        feed_class = getattr(cryptofeed_exchanges, venue.feed_class)
    try:
        with quiet_loggers("cryptofeed.connection", "cryptofeed.exchange"):
            return feed_class, feed_class.symbols(), None
    except Exception as e:
        return feed_class, None, f"{type(e).__name__}: {str(e)[:160]}"


def watch_skipped(skipped, interval):
    """Exit when a skipped venue becomes reachable; the supervisor then restarts this process."""

    def loop():
        while True:
            time.sleep(interval)
            for name in list(skipped):
                _, available, error = probe(perps.VENUES[name])
                if error is None and available:
                    print(f"[perps] {name} is reachable now; restarting to pick it up")
                    sys.stdout.flush()
                    os._exit(0)

    threading.Thread(target=loop, name="perps-reprobe", daemon=True).start()


def main():
    from cryptofeed import FeedHandler
    from cryptofeed.defines import L2_BOOK, TRADES

    names = perps.selected_venues(os.environ.get("PERP_VENUES"))
    reprobe = int(os.environ.get("PERP_REPROBE_SECONDS", "600"))
    questdb_host = "questdb" if os.environ.get("IS_DOCKER") else "127.0.0.1"
    writer = QuestDBWriter(host=questdb_host)

    handler = FeedHandler()
    enabled, skipped = {}, {}
    for name in names:
        venue = perps.VENUES[name]
        feed_class, available, error = probe(venue)
        specs = {}
        if error is None:
            try:
                specs = perps.build_specs(venue, available)
            except Exception as e:
                error = f"contract specs unavailable: {type(e).__name__}: {str(e)[:160]}"
        if error is not None or not specs:
            skipped[name] = error or "none of the wanted symbols are listed"
            print(f"[perps] SKIPPING {name}: {skipped[name]}")
            continue

        symbols = list(specs)
        deep_books = perps.DeepBooks() if venue.deep_sig_figs else None
        for n_sig_figs in venue.deep_sig_figs:
            handler.add_feed(
                aggregated_feed_class(feed_class, n_sig_figs)(
                    channels=[L2_BOOK],
                    symbols=symbols,
                    callbacks={L2_BOOK: [deep_book_callback(deep_books, n_sig_figs)]},
                )
            )
        handler.add_feed(
            feed_class(
                channels=[TRADES],
                symbols=symbols,
                callbacks={TRADES: [trade_callback(writer, specs, venue.aliases)]},
            )
        )
        handler.add_feed(
            feed_class(
                channels=[L2_BOOK],
                symbols=symbols,
                max_depth=BOOK_DEPTH,
                callbacks={
                    L2_BOOK: [
                        book_callback(writer, specs, venue.aliases),
                        liquidity_callback(
                            writer, specs, aliases=venue.aliases, deep_books=deep_books
                        ),
                    ]
                },
            )
        )
        enabled[name] = symbols
        summary = ", ".join(f"{venue.stored_symbol(s)} [{specs[s].native_unit}]" for s in symbols)
        print(f"[perps] {name}: {summary}")

    sys.stdout.flush()
    if not enabled:
        print(f"[perps] no venue is available; retrying in {RETRY_SLEEP_SECONDS}s")
        sys.stdout.flush()
        time.sleep(RETRY_SLEEP_SECONDS)
        writer.close()
        return
    if skipped and reprobe > 0:
        watch_skipped(skipped, reprobe)

    try:
        handler.run()
    finally:
        writer.close()


if __name__ == "__main__":
    main()
