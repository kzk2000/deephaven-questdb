"""
Shared pytest fixtures for QuestDB writer tests
"""
import sys
import pytest
from pathlib import Path

# Add src directory to path
src_path = Path(__file__).parent.parent / 'src'
sys.path.insert(0, str(src_path))

from questdb_writer import QuestDBWriter
from collections import OrderedDict
import time


@pytest.fixture
def questdb_host():
    """QuestDB host for testing"""
    return 'localhost'


@pytest.fixture
def writer(questdb_host):
    """Create QuestDB writer instance for testing"""
    w = QuestDBWriter(host=questdb_host, verbose=False)
    yield w
    w.close()


@pytest.fixture
def mock_orderbook():
    """Create a mock orderbook for testing"""
    class MockBook:
        def __init__(self):
            self.exchange = 'TEST'
            self.symbol = 'BTC-USD'
            self.timestamp = time.time()
            
            # Create mock orderbook data
            self.book = type('obj', (object,), {
                'bids': OrderedDict({
                    50000.0: 1.5,
                    49999.0: 2.0,
                    49998.0: 1.0,
                    49997.0: 0.5,
                    49996.0: 3.0
                }),
                'asks': OrderedDict({
                    50001.0: 1.2,
                    50002.0: 0.8,
                    50003.0: 2.5,
                    50004.0: 1.8,
                    50005.0: 0.3
                })
            })()
    
    return MockBook()


@pytest.fixture
def mock_trade():
    """Create a mock trade for testing"""
    return {
        'exchange': 'TEST',
        'symbol': 'BTC-USD',
        'side': 'buy',
        'price': 50000.0,
        'amount': 0.5,
        'id': 'test123',
        'timestamp': time.time(),
        'receipt_timestamp': time.time(),
        'type': 'limit'
    }
