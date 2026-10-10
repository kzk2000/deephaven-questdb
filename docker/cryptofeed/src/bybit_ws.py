"""
cryptofeed's Bybit feed without the REST calls.

Bybit's public WebSocket (stream.bybit.com) is reachable from regions where its REST API
(api.bybit.com) answers HTTP 403, and cryptofeed's Bybit feed needs REST only once: to list
instruments when the feed is created. The instruments we stream (BTC and ETH perpetuals) are
fixed, so they are listed here instead, and the feed subscribes to the venue's deepest book
(1000 levels per side, snapshot then deltas over the same socket) rather than the 200 levels
cryptofeed hard-codes.

    from bybit_ws import bybit_feed_class
    feed_class = bybit_feed_class()        # a cryptofeed Bybit subclass

Written against cryptofeed 3.0.1 (`Bybit._parse_symbol_data`, `subscribe`, the name-mangled
`__reset`); tests/unit/test_pins.py keeps the pin, and the class test below fails on a
cryptofeed upgrade if those moved.
"""

import json

BOOK_DEPTH = 1000  # levels per side; Bybit offers 1, 50, 200, 1000 on linear and inverse


def _instrument(symbol, base, quote, contract_type, tick_size):
    return {
        "symbol": symbol,
        "baseCoin": base,
        "quoteCoin": quote,
        "contractType": contract_type,
        "priceFilter": {"tickSize": tick_size},
    }


# What api.bybit.com /v5/market/instruments-info returns for these contracts, in the shape
# cryptofeed's parser reads. Linear USDC perps are named BTCPERP / ETHPERP by the venue.
STATIC_INSTRUMENTS = [
    {
        "result": {
            "list": [
                _instrument("BTCUSDT", "BTC", "USDT", "LinearPerpetual", "0.10"),
                _instrument("ETHUSDT", "ETH", "USDT", "LinearPerpetual", "0.01"),
                _instrument("BTCPERP", "BTC", "USDC", "LinearPerpetual", "0.10"),
                _instrument("ETHPERP", "ETH", "USDC", "LinearPerpetual", "0.01"),
            ]
        }
    },
    {
        "result": {
            "list": [
                _instrument("BTCUSD", "BTC", "USD", "InversePerpetual", "0.10"),
                _instrument("ETHUSD", "ETH", "USD", "InversePerpetual", "0.01"),
            ]
        }
    },
]


def subscription_messages(subscription, depth, is_book, is_spot=lambda pair: False):
    """JSON subscribe messages for {exchange channel: [pairs]}; books use `depth` levels.

    is_book(channel) says whether a channel name is the order book (cryptofeed registers it as
    an empty string); is_spot(pair) strips the slash in spot pair names.
    """
    messages = []
    for channel, pairs in subscription.items():
        for pair in pairs:
            if is_spot(pair):
                pair = pair.replace("/", "")
            topic = f"orderbook.{depth}.{pair}" if is_book(channel) else f"{channel}.{pair}"
            messages.append(json.dumps({"op": "subscribe", "args": [topic]}))
    return messages


def bybit_feed_class(depth=BOOK_DEPTH):
    """The REST-free Bybit feed class (imports cryptofeed lazily)."""
    from cryptofeed.defines import L2_BOOK
    from cryptofeed.exchanges.bybit import Bybit

    class BybitWS(Bybit):
        @classmethod
        async def _fetch_symbol_data(cls, conn):
            return STATIC_INSTRUMENTS

        async def subscribe(self, connection):
            self._Bybit__reset(connection)  # cryptofeed's private per-connection book reset
            for message in subscription_messages(
                connection.subscription,
                depth,
                is_book=lambda channel: self.exchange_channel_to_std(channel) == L2_BOOK,
            ):
                await connection.write(message)

    return BybitWS
