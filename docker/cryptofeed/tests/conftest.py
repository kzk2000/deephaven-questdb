"""
Shared pytest fixtures for QuestDB writer tests
"""

import sys
import urllib.parse
import urllib.request
import uuid
import pytest
from pathlib import Path

# Add src directory to path
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))

from questdb_writer import QuestDBWriter
from collections import OrderedDict
import time


@pytest.fixture
def questdb_host():
    """QuestDB host for testing"""
    return "localhost"


def _drop_table(host, table):
    """Best-effort DROP TABLE through the REST API."""
    params = urllib.parse.urlencode({"query": f"DROP TABLE IF EXISTS {table}"})
    try:
        urllib.request.urlopen(f"http://{host}:9000/exec?{params}", timeout=10).read()
    except Exception:
        pass


def _wait_for_rows(writer, table, min_rows=1, timeout=15.0):
    """Flush the writer and poll until `table` has at least `min_rows` rows."""
    writer.flush()
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = writer.execute_sql(f"SELECT count() FROM {table}")
        if result and result.get("dataset") and result["dataset"][0][0] >= min_rows:
            return result["dataset"][0][0]
        time.sleep(0.25)
    raise AssertionError(f"{table} did not reach {min_rows} rows within {timeout}s")


@pytest.fixture
def wait_for_rows():
    """Helper: flush a writer and poll until a table has rows."""
    return _wait_for_rows


@pytest.fixture
def writer(questdb_host):
    """QuestDB writer that targets throwaway tables, never the live trades/orderbooks.

    Tables are auto-created by ILP on first write and dropped on teardown.
    """
    suffix = uuid.uuid4().hex[:8]
    w = QuestDBWriter(
        host=questdb_host,
        verbose=False,
        trades_table=f"test_trades_{suffix}",
        orderbooks_table=f"test_orderbooks_{suffix}",
    )
    yield w
    w.close()
    _drop_table(questdb_host, w.trades_table)
    _drop_table(questdb_host, w.orderbooks_table)


@pytest.fixture
def mock_orderbook():
    """Create a mock orderbook for testing"""

    class MockBook:
        def __init__(self):
            self.exchange = "TEST"
            self.symbol = "BTC-USD"
            self.timestamp = time.time()

            # Create mock orderbook data
            self.book = type(
                "obj",
                (object,),
                {
                    "bids": OrderedDict(
                        {
                            50000.0: 1.5,
                            49999.0: 2.0,
                            49998.0: 1.0,
                            49997.0: 0.5,
                            49996.0: 3.0,
                        }
                    ),
                    "asks": OrderedDict(
                        {
                            50001.0: 1.2,
                            50002.0: 0.8,
                            50003.0: 2.5,
                            50004.0: 1.8,
                            50005.0: 0.3,
                        }
                    ),
                },
            )()

    return MockBook()


@pytest.fixture
def mock_trade():
    """Create a mock trade for testing"""
    return {
        "exchange": "TEST",
        "symbol": "BTC-USD",
        "side": "buy",
        "price": 50000.0,
        "amount": 0.5,
        "id": "test123",
        "timestamp": time.time(),
        "receipt_timestamp": time.time(),
        "type": "limit",
    }
