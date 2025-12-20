# Deephaven + QuestDB Real-Time Crypto Analytics

Real-time cryptocurrency analytics platform combining Deephaven (streaming analytics) with 
QuestDB (time-series database persistent storage).

## Quick Start

```bash
# Clone and enter directory
git clone <your-repo-url>
cd deephaven-questdb

# Option 1: Using make (recommended)
make rebuild

# Option 2: Using docker-compose directly
docker-compose build --no-cache
docker-compose down  # Clean any stale containers
docker-compose up -d

# Wait 30 seconds, then access:
# - Deephaven: http://localhost:10000
# - QuestDB:   http://localhost:9000
```

## What This Does

- **Cryptofeed** streams live trades/orderbooks from Coinbase, Bitstamp, and Kraken
- **QuestDB** stores time-series data with WAL (Write-Ahead Log) for efficient change tracking
- **Deephaven** provides real-time analytics and visualization with <1 second latency

## Architecture

```
Crypto Exchanges (Coinbase, Bitstamp, Kraken)
    ↓ WebSocket
Cryptofeed
    ↓ ILP (InfluxDB Line Protocol)
QuestDB (WAL-enabled, time-series optimized)
    ↓ PostgreSQL wire protocol + wal_transactions()
Deephaven (TableDataService custom backend)
    ↓
Real-time Browser UI
```

## Common Commands

```bash
make help       # Show all available commands
make rebuild    # Clean rebuild (first time setup)
make up         # Start services
make down       # Stop services
make ps         # Check status
make logs       # View all logs
make test       # Verify health
```

## Example Usage
From Deephaven UI (http://localhost:10000):

```python
# Test QuestDB connection
exec(open('/data/notebooks/test_tds_minimal.py').read())

# Query recent trades
import qdb
trades = qdb.get_trades(last_nticks=1000)

# Aggregate to candles
candles = qdb.get_candles(sample_by='1m')
```

## Documentation

- **[Main Documentation](docs/)** - Technical documentation and guides
- **[Current Status](docs/CURRENT_STATUS.md)** - System architecture and status
- **[Orderbooks Guide](docs/orderbooks.md)** - How orderbooks work
- **[Known Issues](docs/DEEPHAVEN_BUG_REPORT.md)** - Deephaven WAL bug
- **[Change History](docs/history/)** - Migration and refactoring logs

## License

MIT License - see LICENSE file for details

