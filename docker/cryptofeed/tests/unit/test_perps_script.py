"""
The perps feed callbacks must normalize size and keep the venue's value. No network.
"""

import asyncio
import importlib.util
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

SRC = Path(__file__).parent.parent.parent / "src"
sys.path.insert(0, str(SRC))

import perps  # noqa: E402


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location(
        "cryptofeed_3_perps", SRC / "script" / "cryptofeed_3_perps.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_trade_callback_normalizes_size_and_keeps_native(script):
    writer = MagicMock()
    specs = {"BTC-USD-PERP": perps.INVERSE_USD}
    trade = SimpleNamespace(
        exchange="DERIBIT",
        symbol="BTC-USD-PERP",
        side="sell",
        price=Decimal("83095.5"),
        amount=Decimal("10000"),
        id="451149552",
        timestamp=1_700_000_000.25,
    )
    asyncio.run(script.trade_callback(writer, specs)(trade, 1_700_000_000.3))
    (data,) = writer.write_trade.call_args.args
    assert data["exchange"] == "DERIBIT" and data["symbol"] == "BTC-USD-PERP"
    assert data["amount"] == pytest.approx(10000 / 83095.5)  # base coin
    assert data["size_native"] == 10000.0
    assert data["size_unit"] == "USD"
    assert data["contract_type"] == "inverse"
    assert data["contract_size"] == 1.0
    assert data["notional_usd"] == 10000.0
    assert data["id"] == "451149552"


def test_linear_contract_trade_carries_multiplier_and_notional(script):
    writer = MagicMock()
    specs = {"BTC-USDT-PERP": perps.SizeSpec("base", 0.01, "CONTRACTS")}
    trade = SimpleNamespace(
        exchange="OKX",
        symbol="BTC-USDT-PERP",
        side="buy",
        price=Decimal("83107.6"),
        amount=Decimal("120"),
        id="2986406877",
        timestamp=1_700_000_000.25,
    )
    asyncio.run(script.trade_callback(writer, specs)(trade, 1_700_000_000.3))
    (data,) = writer.write_trade.call_args.args
    assert data["amount"] == pytest.approx(1.2)  # base coin
    assert data["size_native"] == 120.0 and data["size_unit"] == "CONTRACTS"
    assert data["contract_size"] == 0.01 and data["contract_type"] == "linear"
    assert data["notional_usd"] == pytest.approx(1.2 * 83107.6)


class FakeSide(dict):
    """price -> size mapping with the .items() cryptofeed's order book side offers."""


def _fake_book(bids, asks, exchange="DERIBIT", symbol="BTC-USD-PERP", timestamp=1_700_000_000.5):
    return SimpleNamespace(
        exchange=exchange,
        symbol=symbol,
        timestamp=timestamp,
        book=SimpleNamespace(bids=FakeSide(bids), asks=FakeSide(asks)),
    )


def test_liquidity_callback_writes_one_row_with_metrics(script):
    writer = MagicMock()
    specs = {"BTC-USD-PERP": perps.BASE}
    book = _fake_book({Decimal("100"): Decimal("2")}, {Decimal("101"): Decimal("3")})
    asyncio.run(script.liquidity_callback(writer, specs, interval=1.0)(book, 1.0))
    args, kwargs = writer.write_row.call_args
    assert args == ("perps_liquidity",)
    assert kwargs["symbols"] == {
        "exchange": "DERIBIT",
        "symbol": "BTC-USD-PERP",
        "contract_type": "linear",
    }
    cols = kwargs["columns"]
    assert cols["mid"] == 100.5
    assert cols["bid_usd_100bp"] == pytest.approx(200.0)
    assert cols["ask_usd_100bp"] == pytest.approx(303.0)
    assert kwargs["timestamp"] == 1_700_000_000.5


def test_liquidity_callback_converts_inverse_sizes_to_usd_depth(script):
    writer = MagicMock()
    specs = {"BTC-USD-PERP": perps.INVERSE_USD}
    # 10,000 USD at 100 and at 101: base = 100 / 99.0099 coin, depth in USD stays 10,000
    book = _fake_book({Decimal("100"): Decimal("10000")}, {Decimal("101"): Decimal("10000")})
    asyncio.run(script.liquidity_callback(writer, specs)(book, 1.0))
    cols = writer.write_row.call_args.kwargs["columns"]
    assert cols["bid_usd_100bp"] == pytest.approx(10_000.0)
    assert cols["ask_usd_100bp"] == pytest.approx(10_000.0)


def test_liquidity_callback_is_throttled_per_instrument(script):
    writer = MagicMock()
    clock_values = iter([0.0, 0.2, 0.4, 1.1, 1.2])
    cb = script.liquidity_callback(
        writer,
        {"BTC-USD-PERP": perps.BASE, "ETH-USD-PERP": perps.BASE},
        interval=1.0,
        clock=lambda: next(clock_values),
    )
    btc = _fake_book({Decimal("100"): Decimal("1")}, {Decimal("101"): Decimal("1")})
    eth = _fake_book(
        {Decimal("100"): Decimal("1")}, {Decimal("101"): Decimal("1")}, symbol="ETH-USD-PERP"
    )

    async def feed():
        await cb(btc, 1.0)  # t=0.0 emitted
        await cb(btc, 1.0)  # t=0.2 throttled
        await cb(eth, 1.0)  # t=0.4 a different instrument: emitted
        await cb(btc, 1.0)  # t=1.1 interval elapsed: emitted
        await cb(eth, 1.0)  # t=1.2 throttled

    asyncio.run(feed())
    assert writer.write_row.call_count == 3


def test_liquidity_callback_skips_crossed_books_and_survives_errors(script, capsys):
    writer = MagicMock()
    specs = {"BTC-USD-PERP": perps.BASE}
    crossed = _fake_book({Decimal("102"): Decimal("1")}, {Decimal("101"): Decimal("1")})
    asyncio.run(script.liquidity_callback(writer, specs)(crossed, 1.0))
    writer.write_row.assert_not_called()

    writer.write_row.side_effect = RuntimeError("boom")
    ok = _fake_book({Decimal("100"): Decimal("1")}, {Decimal("101"): Decimal("1")})
    asyncio.run(script.liquidity_callback(writer, specs)(ok, 1.0))
    assert "boom" in capsys.readouterr().out


def test_quiet_loggers_silences_then_restores(script):
    import logging

    log = logging.getLogger("cryptofeed.exchange")
    log.setLevel(logging.INFO)
    with script.quiet_loggers("cryptofeed.exchange"):
        assert not log.isEnabledFor(logging.CRITICAL)
    assert log.level == logging.INFO and log.isEnabledFor(logging.ERROR)


def test_quiet_loggers_restores_after_an_exception(script):
    import logging

    log = logging.getLogger("cryptofeed.connection")
    log.setLevel(logging.WARNING)
    with pytest.raises(RuntimeError):
        with script.quiet_loggers("cryptofeed.connection"):
            raise RuntimeError("probe failed")
    assert log.level == logging.WARNING


def test_aliases_rename_the_stored_symbol_everywhere(script):
    writer = MagicMock()
    specs = {"BTC-USD-PERP": perps.BASE}
    aliases = {"BTC-USD-PERP": "BTC-USDC-PERP"}

    trade = SimpleNamespace(
        exchange="HYPERLIQUID",
        symbol="BTC-USD-PERP",
        side="buy",
        price=Decimal("83000"),
        amount=Decimal("0.5"),
        id="1",
        timestamp=1.0,
    )
    asyncio.run(script.trade_callback(writer, specs, aliases)(trade, 1.0))
    (data,) = writer.write_trade.call_args.args
    assert data["symbol"] == "BTC-USDC-PERP"
    assert data["amount"] == 0.5  # base coin, not divided by price
    assert data["contract_type"] == "linear"

    book = _fake_book(
        {Decimal("100"): Decimal("1")},
        {Decimal("101"): Decimal("1")},
        exchange="HYPERLIQUID",
        symbol="BTC-USD-PERP",
    )
    asyncio.run(script.book_callback(writer, specs, aliases)(book, 1.0))
    assert writer.write_orderbook.call_args.kwargs["symbol"] == "BTC-USDC-PERP"

    asyncio.run(script.liquidity_callback(writer, specs, aliases=aliases)(book, 1.0))
    assert writer.write_row.call_args.kwargs["symbols"]["symbol"] == "BTC-USDC-PERP"


def test_trade_callback_ignores_unlisted_symbols(script):
    writer = MagicMock()
    trade = SimpleNamespace(
        exchange="OKX", symbol="DOGE-USDT-PERP", side="buy", price=1, amount=1, id="1", timestamp=1
    )
    asyncio.run(script.trade_callback(writer, {})(trade, 1.0))
    writer.write_trade.assert_not_called()


def test_book_callback_passes_the_size_transform(script):
    writer = MagicMock()
    spec = perps.SizeSpec("base", 0.01, "CONTRACTS")
    book = SimpleNamespace(exchange="OKX", symbol="BTC-USDT-PERP")
    asyncio.run(script.book_callback(writer, {"BTC-USDT-PERP": spec})(book, 5.0))
    args, kwargs = writer.write_orderbook.call_args
    assert args == (book, 5.0)
    assert kwargs["depth"] == script.ORDERBOOK_DEPTH
    sizes = kwargs["size_fn"](np.array([1.0]), np.array([120.0]))
    assert sizes[0] == pytest.approx(1.2)


def test_book_callback_survives_a_write_error(script, capsys):
    writer = MagicMock()
    writer.write_orderbook.side_effect = RuntimeError("boom")
    book = SimpleNamespace(exchange="OKX", symbol="BTC-USDT-PERP")
    asyncio.run(script.book_callback(writer, {"BTC-USDT-PERP": perps.BASE})(book, 5.0))
    assert "boom" in capsys.readouterr().out


def test_aggregated_feed_subscribes_with_n_sig_figs(script):
    import json

    class FakeFeed:
        def _reset(self):
            pass

    feed_class = script.aggregated_feed_class(FakeFeed, 3)
    assert feed_class.__name__ == "FakeFeedAgg3"
    sent = []

    class FakeConn:
        subscription = {"l2Book": ["BTC", "ETH"]}

        async def write(self, message):
            sent.append(json.loads(message))

    asyncio.run(feed_class().subscribe(FakeConn()))
    assert [m["subscription"] for m in sent] == [
        {"type": "l2Book", "coin": "BTC", "nSigFigs": 3},
        {"type": "l2Book", "coin": "ETH", "nSigFigs": 3},
    ]
    assert all(m["method"] == "subscribe" for m in sent)


def test_liquidity_callback_extends_the_raw_book_with_deep_books(script):
    writer = MagicMock()
    specs = {"BTC-USD-PERP": perps.BASE}
    deep = perps.DeepBooks()
    # the raw book is 1 level a side; the 3-figure book carries 5 BTC ~60 bps from the mid
    deep.update(
        "BTC-USD-PERP",
        3,
        np.array([82_500.0]),
        np.array([5.0]),
        np.array([83_500.0]),
        np.array([5.0]),
    )
    book = _fake_book(
        {Decimal("83000"): Decimal("1")}, {Decimal("83010"): Decimal("1")}, symbol="BTC-USD-PERP"
    )
    asyncio.run(script.liquidity_callback(writer, specs, deep_books=deep)(book, 1.0))
    cols = writer.write_row.call_args.kwargs["columns"]
    assert cols["bid_levels"] == 2 and cols["ask_levels"] == 2
    mid = 83_005.0
    assert cols["bid_reach_bps"] == pytest.approx((mid - 82_500) / mid * 1e4)
    assert cols["bid_usd_25bp"] == pytest.approx(83_000.0)  # the deep level is ~60 bps out
    assert cols["bid_usd_100bp"] == pytest.approx(83_000.0 + 5 * 82_500.0)
    assert cols["ask_usd_100bp"] == pytest.approx(83_010.0 + 5 * 83_500.0)

    # without deep books the same raw book is unchanged
    writer.reset_mock()
    asyncio.run(script.liquidity_callback(writer, specs)(book, 1.0))
    assert writer.write_row.call_args.kwargs["columns"]["bid_levels"] == 1
