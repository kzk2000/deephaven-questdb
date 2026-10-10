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
