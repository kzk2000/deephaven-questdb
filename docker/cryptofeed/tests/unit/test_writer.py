"""
Unit tests for QuestDB writer basic functionality
"""

import sys
from pathlib import Path

# Add src directory to path
src_path = Path(__file__).parent.parent.parent / "src"
sys.path.insert(0, str(src_path))

from questdb_writer import QuestDBWriter


def test_writer_verbose_mode():
    """Test verbose mode can be disabled"""
    # This should not print anything
    writer = QuestDBWriter(host="localhost", verbose=False)
    assert writer.verbose is False
    writer.close()


def test_writer_connection_attributes():
    """Test writer has correct connection attributes"""
    writer = QuestDBWriter(
        host="testhost", ilp_port=9999, http_port=8888, protocol="http", verbose=False
    )

    assert writer.host == "testhost"
    assert writer.ilp_port == 9999
    assert writer.http_port == 8888
    assert writer.base_url == "http://testhost:8888/exec"
    assert writer.conf == "http::addr=testhost:9999;"

    writer.close()


def test_writer_default_tables():
    """Test default destination tables and that they are configurable"""
    writer = QuestDBWriter(host="localhost", verbose=False)
    assert writer.trades_table == "trades"
    assert writer.orderbooks_table == "orderbooks"
    writer.close()

    writer = QuestDBWriter(
        host="localhost", verbose=False, trades_table="t_x", orderbooks_table="o_x"
    )
    assert writer.trades_table == "t_x"
    assert writer.orderbooks_table == "o_x"
    writer.close()


def test_writer_has_methods():
    """Test writer instance has all required methods"""
    writer = QuestDBWriter(host="localhost", verbose=False)

    assert callable(writer.write_trade)
    assert callable(writer.write_orderbook)
    assert callable(writer.execute_sql)
    assert callable(writer.flush)
    assert callable(writer.close)

    writer.close()


def test_writer_close_is_safe():
    """Test close() can be called multiple times safely"""
    writer = QuestDBWriter(host="localhost", verbose=False)

    # Should not raise exception
    writer.close()
    writer.close()  # Second close should be safe


def test_default_protocol_is_qwp_with_short_auto_flush(mock_connect, mock_sender):
    writer = QuestDBWriter(host="questdb", verbose=False)
    assert writer.protocol == "ws"
    assert writer.conf == "ws::addr=questdb:9000;auto_flush_interval=25;"
    mock_connect.assert_called_once()
    assert mock_connect.call_args.args[0] == writer.conf
    assert mock_connect.call_args.kwargs["error_handler"] is writer._error_handler
    mock_sender.from_conf.assert_not_called()
    mock_connect.return_value.sender.return_value.__enter__.assert_called_once()
    writer.close()


def test_env_var_selects_ilp_http(monkeypatch, mock_connect, mock_sender):
    monkeypatch.setenv("QUESTDB_PROTOCOL", "http")
    writer = QuestDBWriter(host="questdb", verbose=False)
    assert writer.protocol == "http"
    assert writer.conf == "http::addr=questdb:9000;"
    mock_sender.from_conf.assert_called_once_with("http::addr=questdb:9000;")
    mock_connect.assert_not_called()
    writer.close()


def test_explicit_protocol_beats_env(monkeypatch, mock_connect):
    monkeypatch.setenv("QUESTDB_PROTOCOL", "http")
    writer = QuestDBWriter(host="questdb", protocol="ws", auto_flush_interval_ms=10, verbose=False)
    assert writer.conf == "ws::addr=questdb:9000;auto_flush_interval=10;"
    writer.close()


def test_rows_are_appended_without_per_tick_flush(mock_connect):
    writer = QuestDBWriter(host="questdb", verbose=False)
    sender = mock_connect.return_value.sender.return_value
    writer.write_trade(
        {
            "exchange": "COINBASE",
            "symbol": "BTC-USD",
            "side": "buy",
            "price": 1.0,
            "amount": 2.0,
            "id": "7",
            "timestamp": 1_700_000_000.5,
            "receipt_timestamp": 1_700_000_000.6,
        }
    )
    sender.row.assert_called_once()
    assert sender.row.call_args.args[0] == "trades"
    sender.flush.assert_not_called()  # the background flush interval publishes it
    writer.close()


def _trade(**overrides):
    base = {
        "exchange": "KRAKEN",
        "symbol": "BTC-USD",
        "side": "sell",
        "price": 1.0,
        "amount": 2.0,
        "timestamp": 1_700_000_000.5,
        "receipt_timestamp": 1_700_000_000.6,
    }
    base.update(overrides)
    return base


def _written_trade_ids(sender):
    return [call.kwargs["columns"]["trade_id"] for call in sender.row.call_args_list]


def test_trade_id_is_passed_through_as_a_string(mock_connect):
    writer = QuestDBWriter(host="questdb", verbose=False)
    writer.write_trade(_trade(id=12345))
    writer.write_trade(_trade(id="abc"))
    assert _written_trade_ids(mock_connect.return_value.sender.return_value) == ["12345", "abc"]
    writer.close()


def _last_row(mock_connect):
    return mock_connect.return_value.sender.return_value.row.call_args.kwargs


def test_spot_trades_get_identity_contract_columns(mock_connect):
    writer = QuestDBWriter(host="questdb", verbose=False)
    writer.write_trade(_trade(id="1", price=82000.0, amount=0.5))
    row = _last_row(mock_connect)
    assert row["columns"]["size"] == 0.5
    assert row["columns"]["size_native"] == 0.5
    assert row["columns"]["contract_size"] == 1.0
    assert row["columns"]["notional_usd"] == 41000.0
    assert row["symbols"]["size_unit"] == "BASE"
    assert row["symbols"]["contract_type"] == "spot"
    writer.close()


def test_derivative_columns_are_passed_through(mock_connect):
    writer = QuestDBWriter(host="questdb", verbose=False)
    writer.write_trade(
        _trade(
            id="2",
            price=83037.6,
            amount=0.1305,
            size_native=108.4,
            size_unit="CONTRACTS",
            contract_size=100.0,
            contract_type="inverse",
            notional_usd=10840.0,
        )
    )
    row = _last_row(mock_connect)
    assert row["columns"]["size"] == 0.1305
    assert row["columns"]["size_native"] == 108.4
    assert row["columns"]["contract_size"] == 100.0
    assert row["columns"]["notional_usd"] == 10840.0
    assert row["symbols"]["size_unit"] == "CONTRACTS"
    assert row["symbols"]["contract_type"] == "inverse"
    writer.close()


def test_missing_trade_id_gets_a_unique_synthetic_id(mock_connect):
    """trades is deduplicated on trade_id; a NULL id would merge distinct trades."""
    writer = QuestDBWriter(host="questdb", verbose=False)
    writer.write_trade(_trade())  # no id key
    writer.write_trade(_trade(id=None))
    writer.write_trade(_trade(id=""))
    ids = _written_trade_ids(mock_connect.return_value.sender.return_value)
    assert len(ids) == 3 and len(set(ids)) == 3
    assert all(i.startswith("rx-") for i in ids)
    writer.close()


def test_close_waits_for_acks_and_releases_connection(mock_connect):
    writer = QuestDBWriter(host="questdb", verbose=False)
    db = mock_connect.return_value
    sender = db.sender.return_value
    writer.close()
    sender.flush.assert_called_with(wait=True)
    sender.__exit__.assert_called_once()
    db.close.assert_called_once()
    writer.close()  # idempotent
    db.close.assert_called_once()


def test_error_handler_does_not_reference_the_writer(mock_connect):
    """A handler holding the writer forms a cycle that breaks interpreter shutdown."""
    import gc

    writer = QuestDBWriter(host="questdb", verbose=False)
    handler = writer._error_handler
    referents = gc.get_referents(handler) + [c.cell_contents for c in handler.__closure__]
    assert not any(r is writer for r in referents)
    writer.close()


def test_rejections_are_counted(mock_connect, capsys):
    writer = QuestDBWriter(host="questdb", verbose=False)
    assert writer.rejections == 0
    writer._error_handler("SCHEMA_MISMATCH: column price")
    assert writer.rejections == 1
    assert "SCHEMA_MISMATCH" in capsys.readouterr().out
    writer.close()


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
