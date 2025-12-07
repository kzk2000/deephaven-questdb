# Deephaven + QuestDB Real-Time Crypto Analytics

Real-time cryptocurrency analytics platform combining Deephaven (streaming analytics) with QuestDB (time-series database).

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

## Documentation

- **[Setup Guide](README_SETUP.md)** - Detailed setup instructions and troubleshooting
- **[Bug Report](DEEPHAVEN_BUG_REPORT.md)** - Known TableDataService API issue
- **[Upgrade Notes](UPGRADE_SUMMARY.md)** - Deephaven edge build details

## Requirements

- Docker and Docker Compose
- 16GB RAM recommended (12GB heap for Deephaven)
- Ports: 8080, 8812, 9000, 9009, 9092, 10000, 29092

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

## Troubleshooting

**ContainerConfig error after rebuild:**
```bash
docker-compose down
docker-compose up -d
```

**Services not responding:**
```bash
docker-compose ps        # Check status
docker-compose logs -f   # View logs
```

See [Setup Guide](README_SETUP.md) for detailed troubleshooting.

## Project Structure

```
├── docker/
│   ├── cryptofeed/      # Cryptocurrency data streamer
│   └── deephaven/       # Analytics engine config
├── data/
│   ├── deephaven/       # Notebooks and configs
│   └── questdb/         # Database files (gitignored)
├── docker-compose.yml   # Service orchestration
└── Makefile            # Convenience commands
```

## Development

See [Setup Guide](README_SETUP.md) for development workflow.

---

**Background**: This project adds persistence to Deephaven Community Edition by integrating QuestDB as a time-series backend, with real-time data ingestion from cryptocurrency exchanges via Cryptofeed.
