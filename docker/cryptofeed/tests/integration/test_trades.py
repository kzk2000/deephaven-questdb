"""
Integration tests for trade writing to QuestDB

Requires QuestDB to be running on localhost:9000. Writes go to throwaway tables.
"""

import sys
import time
from pathlib import Path

import pytest

# Add src directory to path
src_path = Path(__file__).parent.parent.parent / "src"
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
    query = f"SELECT count() FROM {writer.trades_table} WHERE symbol = 'BTC-USD'"
    result = writer.execute_sql(query)

    assert result is not None
    assert "dataset" in result
    count = result["dataset"][0][0]
    assert count > 0, "Should have at least one trade row"


def test_trade_data_format(writer, mock_trade):
    """Test that trade data is stored in correct format"""
    trade = mock_trade

    # Write trade
    writer.write_trade(trade)
    writer.flush()
    time.sleep(2)

    # Query the latest trade
    query = f"""
        SELECT exchange, symbol, side, price, size 
        FROM {writer.trades_table}
        WHERE symbol = 'BTC-USD' 
        ORDER BY timestamp DESC 
        LIMIT 1
    """
    result = writer.execute_sql(query)

    assert result is not None
    assert "dataset" in result

    row = result["dataset"][0]
    exchange, symbol, side, price, size = row

    # Verify data
    assert exchange == "TEST"
    assert symbol == "BTC-USD"
    assert side == "buy"
    assert price == 50000.0
    assert size == 0.5


def test_multiple_trades(writer, mock_trade):
    """Test writing multiple trades"""
    # Write 5 trades
    for i in range(5):
        trade = mock_trade.copy()
        trade["price"] = 50000.0 + i
        writer.write_trade(trade)

    writer.flush()
    time.sleep(2)

    # Query count
    query = f"SELECT count() FROM {writer.trades_table}"
    result = writer.execute_sql(query)

    count = result["dataset"][0][0]
    assert count >= 5, "Should have at least 5 trades"


def test_replayed_trades_are_deduplicated(writer, mock_trade, wait_for_rows):
    """Same trade re-sent (restart replay, reconnect) must not add a row; distinct trades must."""
    import init_questdb_tables as init

    table = writer.trades_table
    result = writer.execute_sql(
        f"CREATE TABLE {table} (timestamp TIMESTAMP_NS, exchange SYMBOL, symbol SYMBOL, "
        "side SYMBOL, price DOUBLE, size DOUBLE, trade_id VARCHAR) "
        "TIMESTAMP(timestamp) PARTITION BY DAY WAL"
    )
    assert result is not None and "error" not in result
    keys = ", ".join(init.TRADES_DEDUP_KEYS)
    result = writer.execute_sql(f"ALTER TABLE {table} DEDUP ENABLE UPSERT KEYS({keys})")
    assert result is not None and "error" not in result

    writer.write_trade(mock_trade)
    writer.write_trade(mock_trade)  # exact replay
    other = dict(mock_trade, id="another-trade")  # same timestamp, different trade
    writer.write_trade(other)
    writer.flush(wait=True)
    wait_for_rows(writer, table, min_rows=2)
    time.sleep(1)  # let any (wrongly) surviving duplicate land too

    count = writer.execute_sql(f"SELECT count() FROM {table}")["dataset"][0][0]
    assert count == 2, "replay must collapse, distinct trade ids must both be kept"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
