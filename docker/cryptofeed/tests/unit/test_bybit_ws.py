"""
Unit tests for the REST-free Bybit feed. The pure parts run anywhere; the class tests need
cryptofeed (Python 3.13) and are skipped when it is not installed.
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import bybit_ws  # noqa: E402
import perps  # noqa: E402


def test_subscription_messages_use_the_deep_book_and_plain_topics():
    messages = bybit_ws.subscription_messages(
        {"": ["BTCUSDT", "BTCUSD"], "publicTrade": ["BTCUSDT"]},
        1000,
        is_book=lambda channel: channel == "",
    )
    assert [json.loads(m) for m in messages] == [
        {"op": "subscribe", "args": ["orderbook.1000.BTCUSDT"]},
        {"op": "subscribe", "args": ["orderbook.1000.BTCUSD"]},
        {"op": "subscribe", "args": ["publicTrade.BTCUSDT"]},
    ]


def test_static_instruments_cover_every_symbol_the_venue_wants():
    listed = {
        (i["baseCoin"], i["quoteCoin"])
        for msg in bybit_ws.STATIC_INSTRUMENTS
        for i in msg["result"]["list"]
    }
    wanted = {tuple(s.split("-")[:2]) for s in perps.VENUES["bybit"].symbols}
    assert wanted == listed
    # more than one message: cryptofeed hands a lone response to its parser unwrapped
    assert len(bybit_ws.STATIC_INSTRUMENTS) > 1


def test_bybit_venue_uses_the_websocket_only_feed():
    assert perps.VENUES["bybit"].feed_factory == "bybit_ws:bybit_feed_class"


@pytest.fixture(scope="module")
def feed_class():
    pytest.importorskip("cryptofeed")
    return bybit_ws.bybit_feed_class()


def test_symbols_resolve_without_any_rest_call(feed_class):
    symbols = set(feed_class.symbols(refresh=True))
    assert set(perps.VENUES["bybit"].symbols) <= symbols
    mapping = feed_class.symbol_mapping()
    assert mapping["BTC-USDT-PERP"] == "BTCUSDT"
    assert mapping["BTC-USDC-PERP"] == "BTCPERP"
    assert mapping["BTC-USD-PERP"] == "BTCUSD"
    assert mapping["ETH-USD-PERP"] == "ETHUSD"


def test_feed_subscribes_to_1000_level_books(feed_class):
    from cryptofeed.defines import L2_BOOK, TRADES

    feed = feed_class(channels=[L2_BOOK, TRADES], symbols=["BTC-USDT-PERP", "BTC-USD-PERP"])
    sent = []

    class Conn:
        subscription = {"": ["BTCUSDT"], "publicTrade": ["BTCUSDT"]}

        async def write(self, message):
            sent.append(json.loads(message)["args"][0])

    asyncio.run(feed.subscribe(Conn()))
    assert sent == ["orderbook.1000.BTCUSDT", "publicTrade.BTCUSDT"]
