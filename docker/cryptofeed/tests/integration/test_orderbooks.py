"""
Integration tests for orderbook writing to QuestDB

Requires QuestDB to be running on localhost:9000
"""

import sys
from pathlib import Path
import time
import pytest

# Add src directory to path
src_path = Path(__file__).parent.parent.parent / "src"
sys.path.insert(0, str(src_path))


def test_write_orderbook_to_questdb(writer, mock_orderbook):
    """Test writing orderbook to QuestDB"""
    book = mock_orderbook

    # Write orderbook
    result = writer.write_orderbook(book, book.timestamp, depth=5)

    assert result == True, "Orderbook write should succeed"

    # Wait for data to be committed
    time.sleep(1)

    # Verify data was written
    query = "SELECT count() FROM orderbooks WHERE symbol = 'BTC-USD'"
    result = writer.execute_sql(query)

    assert result is not None
    assert "dataset" in result
    count = result["dataset"][0][0]
    assert count > 0, "Should have at least one orderbook row"


def test_orderbook_data_format(writer, mock_orderbook):
    """Test that orderbook data is stored in correct format"""
    book = mock_orderbook

    # Write orderbook
    writer.write_orderbook(book, book.timestamp, depth=3)
    time.sleep(1)

    # Query the latest row
    query = """
        SELECT exchange, symbol, bids, asks 
        FROM orderbooks 
        WHERE symbol = 'BTC-USD' 
        ORDER BY timestamp DESC 
        LIMIT 1
    """
    result = writer.execute_sql(query)

    assert result is not None
    assert "dataset" in result

    row = result["dataset"][0]
    exchange, symbol, bids, asks = row

    # Verify basic data
    assert exchange == "TEST"
    assert symbol == "BTC-USD"

    # Verify bids/asks are arrays
    assert isinstance(bids, list), "Bids should be a list"
    assert isinstance(asks, list), "Asks should be a list"
    assert len(bids) == 2, "Bids should have 2 arrays (prices, volumes)"
    assert len(asks) == 2, "Asks should have 2 arrays (prices, volumes)"


def test_orderbook_depth_limit(writer, mock_orderbook):
    """Test that orderbook depth limiting works"""
    book = mock_orderbook

    # Write with depth=2
    writer.write_orderbook(book, book.timestamp, depth=2)
    time.sleep(1)

    # Query and check
    query = """
        SELECT bids, asks 
        FROM orderbooks 
        WHERE symbol = 'BTC-USD' 
        ORDER BY timestamp DESC 
        LIMIT 1
    """
    result = writer.execute_sql(query)

    row = result["dataset"][0]
    bids, asks = row

    # Should only have 2 price levels
    assert len(bids[0]) == 2, "Should have exactly 2 bid prices"
    assert len(bids[1]) == 2, "Should have exactly 2 bid volumes"
    assert len(asks[0]) == 2, "Should have exactly 2 ask prices"
    assert len(asks[1]) == 2, "Should have exactly 2 ask volumes"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
