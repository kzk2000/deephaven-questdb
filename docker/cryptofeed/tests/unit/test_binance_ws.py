"""
Unit tests for the REST-free Binance futures feed. The pure parts run anywhere; the class
tests need cryptofeed (Python 3.13) and are skipped when it is not installed.
"""

import asyncio
import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import binance_ws  # noqa: E402
import perps  # noqa: E402


def test_partial_depth_names_replace_only_depth_streams():
    names = ["btcusdt@depth@100ms", "ethusdt@depth", "btcusdt@aggTrade", "btcusdt@bookTicker"]
    assert binance_ws.partial_depth_names(names) == [
        "btcusdt@depth20@100ms",
        "ethusdt@depth20",
        "btcusdt@aggTrade",
        "btcusdt@bookTicker",
    ]
    assert binance_ws.partial_depth_names(["btcusdt@depth@100ms"], 5) == ["btcusdt@depth5@100ms"]


def test_static_symbols_cover_every_symbol_the_venue_wants():
    listed = {s["symbol"] for s in binance_ws.STATIC_EXCHANGE_INFO["symbols"]}
    wanted = {s.replace("-USDT-PERP", "USDT") for s in perps.VENUES["binance_futures"].symbols}
    assert listed == wanted


def test_binance_venue_uses_the_websocket_only_feed():
    assert perps.VENUES["binance_futures"].feed_factory == "binance_ws:binance_feed_class"


@pytest.fixture(scope="module")
def feed_class():
    pytest.importorskip("cryptofeed")
    return binance_ws.binance_feed_class()


def test_symbols_resolve_without_any_rest_call(feed_class):
    assert set(feed_class.symbols(refresh=True)) >= {"BTC-USDT-PERP", "ETH-USDT-PERP"}
    mapping = feed_class.symbol_mapping()
    assert mapping["BTC-USDT-PERP"] == "BTCUSDT"
    assert mapping["ETH-USDT-PERP"] == "ETHUSDT"


def test_feed_subscribes_to_partial_book_and_aggtrade(feed_class):
    from cryptofeed.defines import L2_BOOK, TRADES

    feed = feed_class(channels=[L2_BOOK, TRADES], symbols=["BTC-USDT-PERP", "ETH-USDT-PERP"])
    names = feed._stream_names()
    assert "btcusdt@depth20@100ms" in names and "ethusdt@depth20@100ms" in names
    assert "btcusdt@aggTrade" in names
    assert not any(n.endswith("@depth@100ms") for n in names)


def test_partial_book_message_replaces_the_book_and_calls_back(feed_class):
    from cryptofeed.defines import L2_BOOK

    feed = feed_class(channels=[L2_BOOK], symbols=["BTC-USDT-PERP"])
    seen = []

    async def book_callback(channel, book, receipt_timestamp, **kwargs):
        seen.append((channel, dict(book.book.bids), dict(book.book.asks), kwargs["timestamp"]))

    feed.book_callback = book_callback
    msg = {
        "e": "depthUpdate",
        "E": 1_700_000_000_000,
        "T": 1_700_000_000_005,
        "u": 7,
        "b": [["83000.0", "1.5"], ["82999.9", "2"]],
        "a": [["83000.1", "3"]],
    }
    asyncio.run(feed._book(msg, "BTCUSDT", 1.0))
    # a second message with different levels is a replacement, not a delta
    msg2 = {
        "e": "depthUpdate",
        "E": 1_700_000_000_100,
        "T": 1_700_000_000_105,
        "u": 8,
        "b": [["83001.0", "1"]],
        "a": [["83001.1", "2"]],
    }
    asyncio.run(feed._book(msg2, "BTCUSDT", 1.1))
    assert seen[0][1] == {Decimal("83000.0"): Decimal("1.5"), Decimal("82999.9"): Decimal("2")}
    assert seen[1][1] == {Decimal("83001.0"): Decimal("1")}
    assert seen[1][2] == {Decimal("83001.1"): Decimal("2")}
    assert seen[0][3] == pytest.approx(1_700_000_000.005)
