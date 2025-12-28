"""
Test that all imports work correctly after unification
"""

import sys
from pathlib import Path

# Add src directory to path
src_path = Path(__file__).parent.parent.parent / "src"
sys.path.insert(0, str(src_path))


def test_unified_writer_import():
    """Test that QuestDBWriter can be imported from unified module"""
    from questdb_writer import QuestDBWriter

    assert QuestDBWriter is not None


def test_writer_has_all_methods():
    """Test that unified writer has all required methods"""
    from questdb_writer import QuestDBWriter

    assert hasattr(QuestDBWriter, "__init__")
    assert hasattr(QuestDBWriter, "write_trade")
    assert hasattr(QuestDBWriter, "write_orderbook")
    assert hasattr(QuestDBWriter, "execute_sql")
    assert hasattr(QuestDBWriter, "flush")
    assert hasattr(QuestDBWriter, "close")


def test_writer_initialization():
    """Test writer initialization with various parameters"""
    from questdb_writer import QuestDBWriter

    # Default initialization
    writer1 = QuestDBWriter(host="localhost", verbose=False)
    assert writer1.host == "localhost"
    assert writer1.ilp_port == 9009
    assert writer1.http_port == 9000
    writer1.close()

    # Custom ports
    writer2 = QuestDBWriter(
        host="localhost", ilp_port=9010, http_port=9001, verbose=False
    )
    assert writer2.ilp_port == 9010
    assert writer2.http_port == 9001
    writer2.close()


def test_writer_has_ilp_and_rest_support():
    """Test that writer initializes both ILP and REST connections"""
    from questdb_writer import QuestDBWriter

    writer = QuestDBWriter(host="localhost", verbose=False)

    # Check ILP attributes
    assert hasattr(writer, "ilp_sock")
    assert hasattr(writer, "ilp_port")

    # Check REST attributes
    assert hasattr(writer, "base_url")
    assert hasattr(writer, "http_port")
    assert "http://" in writer.base_url

    writer.close()


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
