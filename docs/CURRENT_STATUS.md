# Current System Status

**Last Updated:** October 10, 2026

## Architecture Overview

### Data Ingestion
- **Stack:** QuestDB 10.0.1, Deephaven server-ui 42.6 (Python 3.12), cryptofeed 3.0.1 (Python 3.13)
- **SDK Version:** questdb==5.0.0 (official QuestDB Python client)
- **Perpetuals:** BTC/ETH linear and inverse perps from Deribit, OKX, Hyperliquid and Bybit stream into `trades` / `orderbooks`, with per-second liquidity metrics in `perps_liquidity` (Bybit over WebSocket only; Binance (USDT-M only) is enabled but region-blocked from this host; see CLAUDE.md)
- **Coinbase:** no-auth override of cryptofeed's Coinbase feed (`docker/cryptofeed/src/coinbase.py`) on the public Exchange websocket; Bitstamp and Kraken use cryptofeed's own feeds
- **Protocol:** QWP over WebSocket (port 9000), 25 ms client flush interval; ILP/HTTP via `QUESTDB_PROTOCOL=http`
- **Trades:** SDK `row()` per tick; `trades` is deduplicated (`DEDUP UPSERT KEYS(timestamp, exchange, symbol, trade_id)`)
- **Orderbooks:** SDK with 2D numpy arrays (top 20 levels)
- **QuestDB tuning:** `QDB_CAIRO_WRITER_DATA_APPEND_PAGE_SIZE=1M` in docker-compose.yml (the 16M default preallocated ~700 MB for a small 42-column table)
- **Queries:** REST API (port 9000)

### Schema
- **Trades Table:** timestamp (TIMESTAMP_NS), exchange, symbol, side, price, size (base coin), trade_id, plus size_native, size_unit, contract_size, contract_type (spot / linear / inverse) and notional_usd
- **Orderbooks Table:** 2D arrays for efficient storage
  - `bids DOUBLE[][]` - Format: `[[prices...], [volumes...]]`
  - `asks DOUBLE[][]` - Format: `[[prices...], [volumes...]]`
- **perps_liquidity:** one row per perp instrument per ~1 s: mid, spread, USD depth within 1..100 bps, slippage for $10k..$5M orders, book reach (`*_reach_bps`)
- **Materialized views:** `orderbooks_1s` (SAMPLE BY 1s), `perps_liquidity_1m`, `perps_trades_10s`, `spot_trades_10s`

## Data Flow Status

### Row counts
Run `make test` for live row counts of `trades`, `orderbooks` and `orderbooks_1s`.

### Retention
- `trades`: kept (partitioned by day)
- `orderbooks`: partitioned by hour, `TTL 1 HOURS`
- `orderbooks_1s`: materialized view (1-second samples), kept
- `perps_liquidity`: `TTL 3 DAYS`; `perps_trades_10s`, `spot_trades_10s`: `TTL 2 DAYS`

### Services
- ✅ **QuestDB:** Healthy (ports 8812 and 9000 in use; 9009 TCP ILP is exposed but unused)
- ✅ **Deephaven:** Healthy (port 10000)
- ✅ **Cryptofeed:** Running (trades, orderbooks and perps processes, supervised by `cryptofeed_0_startup.sh`)

## Deephaven

- **Dashboard:** `notebooks/perps_dashboard.py` (widget `perps_dashboard`), built on `qdb_feed.feed_query` state tables over the views above; helpers and venue registry in `perps_dash_lib.py`
- **Live tables:** `qdb_backend.py` (TableDataService over QuestDB storage) pages a settled prefix (`qdb_prefix.py`): rows are exact and positions never move, at the cost of `settle_seconds` (default 3 s) latency; a row later than that is counted (`late_rows`) and left out
- **Feeds:** `qdb_feed.py` (`feed_table`, `feed_query`) polls and publishes rows once (~0.25 s latency); `feed_query` is how the dashboard reads materialized views
- **Static:** `qdb.py` (QWP, Arrow; ConnectorX over the Postgres wire as `transport="pg"`)

## Earlier Changes (December 2024)

### SDK Migration (December 2024)
1. **Trades Migration** - Migrated from raw socket ILP to official SDK
2. **Orderbooks Migration** - Migrated to SDK using 2D numpy arrays
3. **Code Simplification** - Removed 53 lines of dead code (-18%)

**Result:** Pure SDK implementation for all data ingestion with zero breaking changes.

See [history/2024-12-sdk-migration/](history/2024-12-sdk-migration/) for detailed migration logs.

### Refactoring (December 2024)
1. **Table Unification** - Unified orderbook writers
2. **Test Cleanup** - Organized test suite
3. **Table Rename** - `orderbooks_compact` → `orderbooks`

See [history/2024-12-refactoring/](history/2024-12-refactoring/) for detailed refactoring logs.

## Known Issues

### Deephaven `TableDataService` bug (fixed)
Fixed upstream as of server-ui 42.6; the image no longer patches it.

### TradingView plugin
`deephaven-plugin-tradingview-lightweight` is alpha (0.0.1). It has no `tvl.legend`, and its `auto_bin` stretched the candle price axis, so `trades_chart_live.py` bins candles with `agg_by`.

## Testing

### Test Suite Structure
```
docker/cryptofeed/tests/
├── unit/           - Unit tests for core functionality
├── integration/    - Integration tests with QuestDB
├── simulation/     - Live feed simulation tests
└── utils/          - Test utilities and helpers
```

### Running Tests
```bash
make test                    # Quick status check (services, tables, row counts)
make pytest
```

See [../docker/cryptofeed/tests/TESTING.md](../docker/cryptofeed/tests/TESTING.md) for test documentation.

## Utility Scripts

Located in `docker/cryptofeed/tests/utils/`:
- `check_trades.py` - Verify trade data integrity
- `verify_data.py` - Check orderbook data structure
- `system_status.py` - System status summary

## Quick Reference

### Start/Stop Services
```bash
make up         # Start all services
make down       # Stop all services
make restart    # Restart all services
make test       # Quick health check
```

### View Logs
```bash
docker logs questdb
docker logs cryptofeed
docker logs deephaven
```

### Access Services
- **QuestDB Console:** http://localhost:9000
- **Deephaven UI:** http://localhost:10000
- **QuestDB REST API:** http://localhost:9000/exec

## Documentation

- [Main README](../README.md) - Project overview and setup
- [Orderbooks Guide](orderbooks.md) - How orderbooks work
- [End-to-end verification (Dec 2025 snapshot)](history/2025-12-end-to-end-verification.md)
- [Change History](history/) - Migration and refactoring logs
