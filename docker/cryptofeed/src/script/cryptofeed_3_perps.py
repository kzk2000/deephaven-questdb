"""
BTC / ETH perpetual futures (linear and inverse) from Binance, Bybit, Deribit and OKX.

Trades and L2 books go to the same `trades` / `orderbooks` tables as the spot feeds.
`exchange` is BINANCE_FUTURES, BINANCE_DELIVERY, BYBIT, DERIBIT or OKX; `symbol` is
BTC-USD-PERP (inverse, coin margined) or BTC-USDT-PERP / BTC-USDC-PERP (linear).

`size` is normalized to base coin so it is comparable with spot. trades also keeps the venue's
own number (size_native, size_unit), the contract multiplier (contract_size, 1 for spot), the
margin type (contract_type: spot / linear / inverse) and the dollar notional (notional_usd);
see perps.py.

Venues that cannot be reached (Binance and Bybit refuse some regions with HTTP 451/403) are
skipped and re-probed every PERP_REPROBE_SECONDS; the process restarts itself when one becomes
reachable so it is picked up without operator action.

Environment:
    PERP_VENUES            comma list from deribit,okx,binance_futures,binance_delivery,bybit
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

import perps
from questdb_writer import QuestDBWriter

ORDERBOOK_DEPTH = 20
RETRY_SLEEP_SECONDS = 60


def trade_callback(writer, specs):
    """Write a trade: `size` in base coin, plus native size, contract multiplier and notional."""

    async def on_trade(trade, receipt_timestamp):
        spec = specs.get(trade.symbol)
        if spec is None:
            return
        price = float(trade.price)
        native = float(trade.amount)
        writer.write_trade(
            {
                "exchange": trade.exchange,
                "symbol": trade.symbol,
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


def book_callback(writer, specs):
    """Write an order book snapshot with level sizes converted to base coin."""

    async def on_book(book, receipt_timestamp):
        spec = specs.get(book.symbol)
        if spec is None:
            return
        try:
            writer.write_orderbook(
                book, receipt_timestamp, depth=ORDERBOOK_DEPTH, size_fn=spec.to_base_array
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


def probe(venue):
    """(feed class, listed symbols, error) for a venue; error is None when reachable."""
    import cryptofeed.exchanges as cryptofeed_exchanges  # lazy: keeps this module importable

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
        handler.add_feed(
            feed_class(
                channels=[TRADES],
                symbols=symbols,
                callbacks={TRADES: [trade_callback(writer, specs)]},
            )
        )
        handler.add_feed(
            feed_class(
                channels=[L2_BOOK],
                symbols=symbols,
                max_depth=ORDERBOOK_DEPTH,
                callbacks={L2_BOOK: [book_callback(writer, specs)]},
            )
        )
        enabled[name] = symbols
        summary = ", ".join(f"{s} [{specs[s].native_unit}]" for s in symbols)
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
