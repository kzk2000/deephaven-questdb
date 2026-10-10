"""
Coinbase Exchange feed that works WITHOUT authentication (local override).

This file replaces cryptofeed's own ``cryptofeed/exchanges/coinbase.py`` at image build
time (see the Dockerfile). It is the "no-auth hack": cryptofeed 3.x talks to the Advanced Trade
API, while this keeps using the public Coinbase Exchange websocket feed, which needs no
credentials for the channels we use:

    wss://ws-feed.exchange.coinbase.com
        matches        -> TRADES
        level2_batch   -> L2_BOOK   (plain "level2" now requires authentication)
        ticker         -> TICKER

Differences from upstream cryptofeed (2.4.0 Coinbase, ported to the 3.0 core):
    - endpoints: ws-feed.exchange.coinbase.com / api.exchange.coinbase.com (the old
      *.pro.coinbase.com hosts are gone)
    - L2 subscription uses the public "level2_batch" channel
    - L2 updates only delete a price level that exists, and ignore zero-size inserts
    - L3 book (authenticated "full" channel) and the REST snapshot mixin are removed

Targets cryptofeed 3.0.1; the Dockerfile asserts the installed version matches.

Based on cryptofeed, Copyright (C) 2017-2026 Bryant Moscon - bmoscon@gmail.com
Please see the cryptofeed LICENSE file for the terms and conditions associated with this software.
"""

import logging
from collections import defaultdict
from decimal import Decimal
from typing import Dict, Tuple

from cryptofeed import _json as json
from cryptofeed.connection import AsyncConnection, RestEndpoint, Routes, WebsocketEndpoint
from cryptofeed.defines import ASK, BID, BUY, COINBASE, L2_BOOK, SELL, TICKER, TRADES
from cryptofeed.feed import Feed
from cryptofeed.symbols import Symbol
from cryptofeed.types import OrderBook, Ticker, Trade

LOG = logging.getLogger(__name__)

# cryptofeed standard channel -> Coinbase Exchange channel actually subscribed to
LEVEL2_PUBLIC_CHANNEL = "level2_batch"


class Coinbase(Feed):
    id = COINBASE
    websocket_endpoints = [
        WebsocketEndpoint("wss://ws-feed.exchange.coinbase.com", options={"compression": None})
    ]
    rest_endpoints = [RestEndpoint("https://api.exchange.coinbase.com", routes=Routes("/products"))]

    websocket_channels = {
        L2_BOOK: "level2",
        TRADES: "matches",
        TICKER: "ticker",
    }
    request_limit = 10

    @classmethod
    def _parse_symbol_data(cls, data: list) -> Tuple[Dict, Dict]:
        ret = {}
        info = defaultdict(dict)

        for entry in data:
            base, quote = entry["id"].split("-")
            sym = Symbol(base, quote)
            info["tick_size"][sym.normalized] = entry["quote_increment"]
            info["instrument_type"][sym.normalized] = sym.type
            ret[sym.normalized] = entry["id"]
        return ret, info

    def __init__(self, callbacks=None, **kwargs):
        super().__init__(callbacks=callbacks, **kwargs)
        self.__reset()

    def __reset(self):
        self._l2_book = {}

    async def _ticker(self, msg: dict, timestamp: float):
        """
        {
            'type': 'ticker',
            'sequence': 5928281348,
            'product_id': 'BTC-USD',
            'price': '8500.00000000',
            'best_bid': '8500',
            'best_ask': '8500.01',
            'side': 'sell',
            'time': '2018-05-21T00:30:11.587000Z',
            'trade_id': 43736677,
            'last_size': '0.00241692'
        }
        """
        ts = self.timestamp_normalize(msg["time"]) if "time" in msg else None
        await self.callback(
            TICKER,
            Ticker(
                self.id,
                self.exchange_symbol_to_std_symbol(msg["product_id"]),
                Decimal(msg["best_bid"]),
                Decimal(msg["best_ask"]),
                ts,
                raw=msg,
            ),
            timestamp,
        )

    async def _trade(self, msg: dict, timestamp: float):
        """
        {
            'type': 'match',  # or 'last_match'
            'trade_id': 43736593,
            'maker_order_id': '2663b65f-b74e-4513-909d-975e3910cf22',
            'taker_order_id': 'd058d737-87f1-4763-bbb4-c2ccf2a40bde',
            'side': 'buy',
            'size': '0.01235647',
            'price': '8506.26000000',
            'product_id': 'BTC-USD',
            'sequence': 5928276661,
            'time': '2018-05-21T00:26:05.585000Z'
        }

        'side' is the maker side of the trade but we report the taker side
        """
        t = Trade(
            self.id,
            self.exchange_symbol_to_std_symbol(msg["product_id"]),
            SELL if msg["side"] == "buy" else BUY,
            Decimal(msg["size"]),
            Decimal(msg["price"]),
            self.timestamp_normalize(msg["time"]),
            id=str(msg["trade_id"]),
            type=None,
            raw=msg,
        )
        await self.callback(TRADES, t, timestamp)

    async def _pair_level2_snapshot(self, msg: dict, timestamp: float):
        pair = self.exchange_symbol_to_std_symbol(msg["product_id"])
        bids = {Decimal(price): Decimal(amount) for price, amount in msg["bids"]}
        asks = {Decimal(price): Decimal(amount) for price, amount in msg["asks"]}
        if pair not in self._l2_book:
            self._l2_book[pair] = OrderBook(
                self.id, pair, max_depth=self.max_depth, bids=bids, asks=asks
            )
        else:
            self._l2_book[pair].book.bids = bids
            self._l2_book[pair].book.asks = asks

        await self.book_callback(L2_BOOK, self._l2_book[pair], timestamp, raw=msg)

    async def _pair_level2_update(self, msg: dict, timestamp: float):
        pair = self.exchange_symbol_to_std_symbol(msg["product_id"])
        if pair not in self._l2_book:
            return  # update before the snapshot; the snapshot replaces all state anyway

        ts = self.timestamp_normalize(msg["time"])
        delta = {BID: [], ASK: []}
        for side, price, amount in msg["changes"]:
            side = BID if side == "buy" else ASK
            price = Decimal(price)
            amount = Decimal(amount)

            if amount == 0:
                if price in self._l2_book[pair].book[side]:
                    del self._l2_book[pair].book[side][price]
                    delta[side].append((price, 0))
            else:
                self._l2_book[pair].book[side][price] = amount
                delta[side].append((price, amount))

        await self.book_callback(
            L2_BOOK, self._l2_book[pair], timestamp, timestamp=ts, raw=msg, delta=delta
        )

    async def message_handler(self, msg: str, conn: AsyncConnection, timestamp: float):
        msg = json.loads(msg, parse_float=Decimal)

        msg_type = msg.get("type")
        if msg_type == "ticker":
            await self._ticker(msg, timestamp)
        elif msg_type in ("match", "last_match"):
            await self._trade(msg, timestamp)
        elif msg_type == "snapshot":
            await self._pair_level2_snapshot(msg, timestamp)
        elif msg_type == "l2update":
            await self._pair_level2_update(msg, timestamp)
        elif msg_type in ("subscriptions", "heartbeat", "activate"):
            pass
        elif msg_type == "error":
            LOG.error("%s: %s", self.id, msg)
        elif msg_type is not None:
            LOG.warning("%s: Invalid message type %s", self.id, msg)

    async def subscribe(self, conn: AsyncConnection):
        self.__reset()

        for chan in self.subscription:
            await conn.write(
                json.dumps(
                    {
                        "type": "subscribe",
                        "product_ids": self.subscription[chan],
                        "channels": [LEVEL2_PUBLIC_CHANNEL if chan == "level2" else chan],
                    }
                )
            )
