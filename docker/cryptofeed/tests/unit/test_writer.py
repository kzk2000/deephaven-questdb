"""
Unit tests for QuestDB writer basic functionality
"""

import sys
from pathlib import Path
from unittest.mock import patch

# Add src directory to path
src_path = Path(__file__).parent.parent.parent / "src"
sys.path.insert(0, str(src_path))

from questdb_writer import QuestDBWriter


def test_writer_verbose_mode():
    """Test verbose mode can be disabled"""
    # This should not print anything
    writer = QuestDBWriter(host="localhost", verbose=False)
    assert writer.verbose == False
    writer.close()


def test_writer_connection_attributes():
    """Test writer has correct connection attributes"""
    # The SDK connects on enter; patch it so no server is needed
    with patch("questdb_writer.Sender"):
        writer = QuestDBWriter(host="testhost", ilp_port=9999, http_port=8888, verbose=False)

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


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
