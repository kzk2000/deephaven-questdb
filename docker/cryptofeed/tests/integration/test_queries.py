"""
Integration tests for SQL query execution via REST API
"""

import sys
from pathlib import Path
import pytest

# Add src directory to path
src_path = Path(__file__).parent.parent.parent / "src"
sys.path.insert(0, str(src_path))


def test_execute_select_query(writer):
    """Test executing SELECT query"""
    result = writer.execute_sql("SELECT count() FROM trades")

    assert result is not None
    assert "dataset" in result
    assert isinstance(result["dataset"], list)


def test_execute_show_tables_query(writer):
    """Test executing SHOW TABLES query"""
    result = writer.execute_sql("SHOW TABLES")

    assert result is not None
    assert "dataset" in result

    # Find our tables
    tables = [row[0] for row in result["dataset"]]
    assert "trades" in tables
    assert "orderbooks" in tables


def test_query_with_filter(writer):
    """Test query with WHERE clause"""
    result = writer.execute_sql("SELECT count() FROM trades WHERE symbol = 'BTC-USD'")

    assert result is not None
    assert "dataset" in result


def test_query_with_limit(writer):
    """Test query with LIMIT"""
    result = writer.execute_sql("SELECT * FROM trades ORDER BY timestamp DESC LIMIT 5")

    assert result is not None
    assert "dataset" in result
    assert len(result["dataset"]) <= 5


def test_query_with_aggregation(writer):
    """Test query with aggregation function"""
    result = writer.execute_sql("SELECT symbol, count() as trade_count FROM trades GROUP BY symbol")

    assert result is not None
    assert "dataset" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
