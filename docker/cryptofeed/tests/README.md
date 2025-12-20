# Cryptofeed QuestDB Tests

This directory contains tests for the cryptofeed-QuestDB integration.

## Test Structure

```
tests/
├── __init__.py           # Test package initialization
├── README.md             # This file
├── conftest.py           # Shared pytest fixtures
├── unit/                 # Unit tests
│   ├── test_writer.py    # QuestDB writer unit tests
│   └── test_imports.py   # Import verification tests
├── integration/          # Integration tests
│   ├── test_orderbooks.py  # Orderbook writing tests
│   ├── test_trades.py      # Trade writing tests
│   └── test_queries.py     # SQL query tests
└── simulation/           # Simulation tests
    └── test_live_feed.py   # Live feed simulation
```

## Running Tests

### All tests
```bash
pytest docker/cryptofeed/tests/
```

### Specific test category
```bash
pytest docker/cryptofeed/tests/unit/
pytest docker/cryptofeed/tests/integration/
pytest docker/cryptofeed/tests/simulation/
```

### Single test file
```bash
pytest docker/cryptofeed/tests/unit/test_writer.py
```

### Verbose output
```bash
pytest -v docker/cryptofeed/tests/
```

## Prerequisites

1. **QuestDB running**: `docker-compose up -d questdb`
2. **Tables initialized**: Tables are created automatically by tests
3. **Python dependencies**: Install from root `requirements.txt`

## Test Categories

### Unit Tests
Test individual components in isolation without external dependencies.
- Writer initialization
- Data formatting
- Error handling

### Integration Tests
Test integration with QuestDB database.
- Writing orderbooks
- Writing trades
- Querying data
- Data validation

### Simulation Tests
Test with simulated live data feeds.
- Mock orderbook feeds
- Mock trade feeds
- Performance tests

## Writing New Tests

### Example Unit Test
```python
import sys
sys.path.insert(0, '../src')
from questdb_writer import QuestDBWriter

def test_writer_initialization():
    writer = QuestDBWriter('localhost', verbose=False)
    assert writer.host == 'localhost'
    assert writer.ilp_port == 9009
    assert writer.http_port == 9000
    writer.close()
```

### Example Integration Test
```python
import sys
sys.path.insert(0, '../src')
from questdb_writer import QuestDBWriter

def test_write_orderbook():
    writer = QuestDBWriter('localhost', verbose=False)
    # Create mock book...
    result = writer.write_orderbook(book, timestamp)
    assert result == True
    writer.close()
```

## Manual Test Scripts

For quick manual testing, use the scripts in the project root:
- `check_trades.py` - Check trades table data
- `verify_data.py` - Verify all tables
- `final_status.py` - Full system status check
