# Deephaven + QuestDB Real-Time Crypto Analytics

Real-time cryptocurrency analytics platform combining Deephaven (streaming analytics) with
QuestDB (time-series database persistent storage).

## Quick Start

```bash
git clone <your-repo-url>
cd deephaven-questdb

make build && make up   # or `make rebuild` for a clean (no-cache) build; then wait ~30 s
make test               # services healthy, tables exist, row counts
```

### Perps liquidity dashboard (start here)

Deribit against the other perpetual-futures venues: depth and slippage matrices, prices and
basis, volume and flow.

1. Open the Deephaven UI: http://localhost:10000
2. Run in the console (takes ~10-15 s):
   ```python
   exec(open('/data/storage/notebooks/perps_dashboard.py').read())
   ```
3. Open the widget full-page: http://localhost:10000/iframe/widget/?name=perps_dashboard

The QuestDB web console is at http://localhost:9000.

## What This Does

- **Cryptofeed** streams live spot trades and order books from Coinbase, Bitstamp and Kraken
  (BTC/ETH/AVAX/SOL-USD; order books BTC-USD), and BTC/ETH perpetual futures (linear and
  inverse) from Deribit, OKX, Hyperliquid and Bybit (WebSocket only, 1000-level books), and
  Binance (USDT-M) where its REST API is reachable (skipped otherwise: HTTP 451 in some regions).
- **QuestDB 10.0.1** stores everything in WAL tables: `trades` (deduplicated on
  timestamp/exchange/symbol/trade_id), `orderbooks` (`DOUBLE[][]` levels, 1 h TTL),
  `perps_liquidity` (per-second spread / depth / slippage computed at ingest from the full book),
  and the materialized views `orderbooks_1s`, `perps_liquidity_1m`, `perps_trades_10s` and
  `spot_trades_10s`.
- **Deephaven 42.6** reads QuestDB as static snapshots, as ticking tables (`qdb_feed`) and
  renders the dashboards.

## Architecture

```
Crypto exchanges (spot: Coinbase, Bitstamp, Kraken; perps: Deribit, OKX, Hyperliquid, ...)
    ↓ WebSocket
Cryptofeed 3.0.1 (Python 3.13)
    ↓ QWP over WebSocket (port 9000, 25 ms client flush)
QuestDB 10.0.1 (WAL tables + materialized views)
    ↓ PostgreSQL wire (8812) / QWP reads
Deephaven server-ui 42.6 (qdb_feed polling feeds, static snapshots, TableDataService backend)
    ↓
Browser UI (http://localhost:10000)
```

## Common Commands

```bash
make help             # Show all available commands
make build            # Build images
make up               # Start services
make rebuild          # Clean no-cache rebuild and start (first time setup)
make build_with_drop  # Drop trades/orderbooks (and their views) and build (wrong timestamp type)
make down             # Stop services
make ps               # Check status
make logs             # View all logs (logs-dh, logs-qdb, logs-cf per service)
make test             # Verify health
make pytest           # Python test suite
```

## Example Usage
From Deephaven UI (http://localhost:10000):

```python
# Perps liquidity dashboard (see above)
exec(open('/data/storage/notebooks/perps_dashboard.py').read())

# Static snapshots (QWP, Arrow)
exec(open('/data/storage/notebooks/trades_static.py').read())

# Live, ticking tables (TableDataService on QuestDB WAL)
exec(open('/data/storage/notebooks/trades_live_via_questdb_wal.py').read())

# TradingView charts (candles + volume, tick lines per exchange)
exec(open('/data/storage/notebooks/trades_chart_live.py').read())

# Query recent trades
import qdb
trades = qdb.get_trades(last_nticks=1000)

# Aggregate to candles
candles = qdb.get_candles(sample_by='1m')

# Consistent ticking tables for dashboards: poll QuestDB, publish each row once
from qdb_feed import feed_table
live_trades = feed_table("trades", history_seconds=900)
```

## Documentation

- **[Main Documentation](docs/)** - Technical documentation and guides
- **[Current Status](docs/CURRENT_STATUS.md)** - System architecture and status
- **[Orderbooks Guide](docs/orderbooks.md)** - How orderbooks work
- **[Change History](docs/history/)** - Migration and refactoring logs
