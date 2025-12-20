"""
Integration tests for trade writing to QuestDB

Requires QuestDB to be running on localhost:9009 (ILP)
"""
import sys
from pathlib import Path
import time
import pytest

# Add src directory to path
src_path = Path(__file__).parent.parent.parent / 'src'
sys.path.insert(0, str(src_path))


def test_write_trade_to_questdb(writer, mock_trade):
    """Test writing trade to QuestDB via ILP"""
    trade = mock_trade
    
    # Write trade (no return value for ILP writes)
    writer.write_trade(trade)
    writer.flush()
    
    # Wait for data to be committed
    time.sleep(2)
    
    # Verify data was written (query via REST API)
    query = "SELECT count() FROM trades WHERE symbol = 'BTC-USD'"
    result = writer.execute_sql(query)
    
    assert result is not None
    assert 'dataset' in result
    count = result['dataset'][0][0]
    assert count > 0, "Should have at least one trade row"


def test_trade_data_format(writer, mock_trade):
    """Test that trade data is stored in correct format"""
    trade = mock_trade
    
    # Write trade
    writer.write_trade(trade)
    writer.flush()
    time.sleep(2)
    
    # Query the latest trade
    query = """
        SELECT exchange, symbol, side, price, size 
        FROM trades 
        WHERE symbol = 'BTC-USD' 
        ORDER BY timestamp DESC 
        LIMIT 1
    """
    result = writer.execute_sql(query)
    
    assert result is not None
    assert 'dataset' in result
    
    row = result['dataset'][0]
    exchange, symbol, side, price, size = row
    
    # Verify data
    assert exchange == 'TEST'
    assert symbol == 'BTC-USD'
    assert side == 'buy'
    assert price == 50000.0
    assert size == 0.5


def test_multiple_trades(writer, mock_trade):
    """Test writing multiple trades"""
    # Write 5 trades
    for i in range(5):
        trade = mock_trade.copy()
        trade['price'] = 50000.0 + i
        writer.write_trade(trade)
    
    writer.flush()
    time.sleep(2)
    
    # Query count
    query = "SELECT count() FROM trades"
    result = writer.execute_sql(query)
    
    count = result['dataset'][0][0]
    assert count >= 5, "Should have at least 5 trades"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
