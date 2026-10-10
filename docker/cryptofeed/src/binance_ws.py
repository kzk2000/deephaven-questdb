"""
cryptofeed's Binance USDT-M futures feed without the REST calls.

Binance's futures WebSocket (fstream.binance.com) is reachable from regions where its REST API
(fapi.binance.com) answers HTTP 451. cryptofeed's BinanceFutures needs REST twice: to list
instruments when the feed is created, and to fetch a snapshot to anchor the incremental
`@depth` stream. Both are replaced here:

  - the BTC and ETH USDT perpetuals are listed statically;
  - the order book comes from the partial book stream `<symbol>@depth20@100ms`, a complete
    top-20 snapshot every 100 ms (no snapshot call, no update-id bookkeeping), the same shape
    Hyperliquid serves. Twenty levels reach only ~0.25 bps from mid for BTC, so every depth
    bucket from 1 bp upward is a lower bound, and `bid/ask_reach_bps` says so.

Trades use the `aggTrade` stream unchanged. Written against cryptofeed 3.0.1
(`BinanceFutures._parse_symbol_data`, `_stream_names`, `_book`).
"""

from decimal import Decimal

BOOK_LEVELS = 20  # partial depth stream sizes: 5, 10, 20


def _symbol(symbol, base, tick_size):
    return {
        "symbol": symbol,
        "status": "TRADING",
        "contractStatus": "TRADING",
        "contractType": "PERPETUAL",
        "baseAsset": base,
        "quoteAsset": "USDT",
        "filters": [{"tickSize": tick_size}],
    }


# What /fapi/v1/exchangeInfo returns for these contracts, in the shape cryptofeed parses.
STATIC_EXCHANGE_INFO = {
    "symbols": [_symbol("BTCUSDT", "BTC", "0.10"), _symbol("ETHUSDT", "ETH", "0.01")]
}


def partial_depth_names(names, levels=BOOK_LEVELS):
    """Turn incremental depth stream names into partial book names:
    'btcusdt@depth@100ms' -> 'btcusdt@depth20@100ms', 'btcusdt@depth' -> 'btcusdt@depth20'.
    Other streams (aggTrade, ...) are unchanged."""
    out = []
    for name in names:
        symbol, _, stream = name.partition("@")
        channel, sep, interval = stream.partition("@")
        if channel == "depth":
            channel = f"depth{levels}"
        out.append(f"{symbol}@{channel}{sep}{interval}")
    return out


def binance_feed_class(levels=BOOK_LEVELS):
    """The REST-free BinanceFutures feed class (imports cryptofeed lazily)."""
    from cryptofeed.defines import L2_BOOK
    from cryptofeed.exchanges.binance_futures import BinanceFutures
    from cryptofeed.types import OrderBook

    class BinanceFuturesWS(BinanceFutures):
        @classmethod
        async def _fetch_symbol_data(cls, conn):
            return [STATIC_EXCHANGE_INFO]

        def _stream_names(self):
            return partial_depth_names(super()._stream_names(), levels)

        async def _book(self, msg, pair, timestamp):
            """A partial book message is the whole book: replace, do not apply deltas."""
            std_pair = self.exchange_symbol_to_std_symbol(pair)
            book = self._l2_book.get(std_pair)
            if book is None:
                book = OrderBook(self.id, std_pair, max_depth=self.max_depth)
                self._l2_book[std_pair] = book
            book.book.bids = {Decimal(p): Decimal(q) for p, q in msg["b"]}
            book.book.asks = {Decimal(p): Decimal(q) for p, q in msg["a"]}
            event_ms = msg.get("T") or msg.get("E")
            await self.book_callback(
                L2_BOOK,
                book,
                timestamp,
                timestamp=self.timestamp_normalize(event_ms) if event_ms else None,
                raw=msg,
                sequence_number=msg.get("u"),
            )

    return BinanceFuturesWS
