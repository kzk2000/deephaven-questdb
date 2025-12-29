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
    assert writer.verbose == False
    writer.close()


def test_writer_connection_attributes():
    """Test writer has correct connection attributes"""
    writer = QuestDBWriter(host="testhost", ilp_port=9999, http_port=8888, verbose=False)

    assert writer.host == "testhost"
    assert writer.ilp_port == 9999
    assert writer.http_port == 8888
    assert writer.base_url == "http://testhost:8888/exec"

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
