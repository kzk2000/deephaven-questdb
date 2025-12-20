# Testing Guide

## Test Organization

The test suite is organized into three categories:

### 1. Unit Tests (`unit/`)
Test individual components without external dependencies.

```bash
# Run unit tests
cd docker/cryptofeed/tests
./run_tests.sh unit

# Or with pytest directly
pytest unit/ -v
```

**Tests:**
- `test_imports.py` - Verify unified writer imports
- `test_writer.py` - Writer initialization and methods

### 2. Integration Tests (`integration/`)
Test integration with QuestDB database.

**Prerequisites:** QuestDB must be running

```bash
# Start QuestDB
docker-compose up -d questdb

# Run integration tests
./run_tests.sh integration

# Or with pytest directly
pytest integration/ -v
```

**Tests:**
- `test_orderbooks.py` - Orderbook writing and verification
- `test_trades.py` - Trade writing via ILP
- `test_queries.py` - SQL query execution via REST

### 3. Simulation Tests (`simulation/`)
Test with simulated live data feeds.

```bash
# Run simulation tests
./run_tests.sh simulation

# Or with pytest directly
pytest simulation/ -v
```

**Tests:**
- `test_live_feed.py` - Simulated orderbook feed with multiple exchanges

## Quick Start

### Run All Tests
```bash
cd docker/cryptofeed/tests
./run_tests.sh all
```

### Run Specific Test File
```bash
pytest unit/test_imports.py -v
pytest integration/test_orderbooks.py -v
```

### Run Single Test Function
```bash
pytest unit/test_imports.py::test_unified_writer_import -v
```

## Utility Scripts

Located in `utils/` for manual testing and verification:

### Check Trades
```bash
python utils/check_trades.py
```
Shows latest trades per symbol and total count.

### Verify Data
```bash
python utils/verify_data.py
```
Displays orderbook data format and content.

### System Status
```bash
python utils/system_status.py
```
Complete system check including data flow and growth rates.

## Writing Tests

### Using Fixtures

The `conftest.py` provides shared fixtures:

```python
def test_with_writer(writer):
    """Writer fixture automatically connects and closes"""
    result = writer.execute_sql("SELECT count() FROM trades")
    assert result is not None

def test_with_mock_data(mock_orderbook, mock_trade):
    """Use pre-configured mock data"""
    assert mock_orderbook.exchange == 'TEST'
    assert mock_trade['symbol'] == 'BTC-USD'
```

### Test Structure

```python
import sys
from pathlib import Path

# Add src directory to path
src_path = Path(__file__).parent.parent.parent / 'src'
sys.path.insert(0, str(src_path))

from questdb_writer import QuestDBWriter

def test_something():
    writer = QuestDBWriter('localhost', verbose=False)
    # ... test code ...
    writer.close()
```

## Continuous Integration

### GitHub Actions Example

```yaml
- name: Run Tests
  run: |
    docker-compose up -d questdb
    sleep 10
    cd docker/cryptofeed/tests
    pytest . -v
```

## Test Coverage

To generate coverage report:

```bash
pytest --cov=../src --cov-report=html
open htmlcov/index.html
```

## Troubleshooting

### QuestDB Not Running
```
Error: QuestDB is not running
```
**Solution:** Start QuestDB first
```bash
docker-compose up -d questdb
```

### Connection Refused
```
ConnectionRefusedError: [Errno 111] Connection refused
```
**Solution:** Wait for QuestDB to fully initialize
```bash
sleep 10  # Wait for startup
```

### Import Errors
```
ModuleNotFoundError: No module named 'questdb_writer'
```
**Solution:** Ensure src path is added correctly
```python
src_path = Path(__file__).parent.parent.parent / 'src'
sys.path.insert(0, str(src_path))
```

### Tests Pass Locally But Fail in CI
- Check QuestDB startup time (increase sleep)
- Verify database is empty before tests
- Check port conflicts

## Performance Testing

For performance benchmarks:

```bash
pytest simulation/test_live_feed.py -v --durations=10
```

Shows slowest 10 tests.

## Best Practices

1. **Isolation**: Each test should be independent
2. **Cleanup**: Use fixtures for setup/teardown
3. **Assertions**: Clear, specific assertions
4. **Speed**: Keep unit tests fast (< 1s each)
5. **Documentation**: Document test purpose and requirements
6. **Data**: Use fixtures for mock data
7. **Coverage**: Aim for >80% code coverage

## Running Tests in Docker

```bash
# Run tests inside cryptofeed container
docker exec cryptofeed pytest /cryptofeed/tests/ -v
```

## Debugging

### Verbose Output
```bash
pytest -vv -s unit/test_imports.py
```

### Stop on First Failure
```bash
pytest -x
```

### Run Last Failed Tests
```bash
pytest --lf
```

### PDB Debugging
```bash
pytest --pdb
```

## Test Matrix

| Test Type | Requires QuestDB | Duration | Purpose |
|-----------|-----------------|----------|---------|
| Unit | No | < 1s | Component logic |
| Integration | Yes | 1-5s | Database operations |
| Simulation | Yes | 10-30s | End-to-end flow |
