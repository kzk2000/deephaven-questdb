# Current System Status

**Last Updated:** October 10, 2026

## Architecture Overview

### Data Ingestion
- **Stack:** QuestDB 10.0.1, Deephaven server-ui 42.6 (Python 3.12), cryptofeed 3.0.1 (Python 3.13)
- **SDK Version:** questdb==5.0.0 (official QuestDB Python client)
- **Perpetuals:** BTC/ETH linear and inverse perps from Deribit and OKX stream into `trades` / `orderbooks` (Binance and Bybit are enabled but region-blocked from this host; see CLAUDE.md)
- **Coinbase:** no-auth override of cryptofeed's Coinbase feed (`docker/cryptofeed/src/coinbase.py`) on the public Exchange websocket; Bitstamp and Kraken use cryptofeed's own feeds
- **Protocol:** QWP over WebSocket (port 9000), 25 ms client flush interval; ILP/HTTP via `QUESTDB_PROTOCOL=http`
- **Trades:** Pure SDK implementation via ILP
- **Orderbooks:** Pure SDK with 2D numpy arrays
- **Queries:** REST API (port 9000)

### Schema
- **Trades Table:** Standard columns (timestamp, exchange, symbol, side, price, size, type)
- **Orderbooks Table:** 2D arrays for efficient storage
  - `bids DOUBLE[][]` - Format: `[[prices...], [volumes...]]`
  - `asks DOUBLE[][]` - Format: `[[prices...], [volumes...]]`
- **Schema Status:** Preserved, zero breaking changes from migration

## Data Flow Status

### Row counts
Run `make test` for live row counts of `trades`, `orderbooks` and `orderbooks_1s`.

### Retention
- `trades`: kept (partitioned by day)
- `orderbooks`: partitioned by hour, `TTL 1 HOURS`
- `orderbooks_1s`: materialized view (1-second samples), kept

### Services
- ✅ **QuestDB:** Healthy (ports 8812 and 9000 in use; 9009 TCP ILP is exposed but unused)
- ✅ **Deephaven:** Healthy (port 10000)
- ✅ **Cryptofeed:** Running (trades + orderbooks processes)

## Recent Major Changes

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

## Code Quality

### QuestDBWriter
- **Lines of code:** 247 (reduced from 292)
- **Complexity:** Low (single initialization pattern)
- **Dead code:** 0 lines
- **Test coverage:** Unit, integration, and simulation tests

### Benefits from Recent Work
- ✅ Pure SDK implementation (official support)
- ✅ Simpler, more maintainable code
- ✅ Better error handling
- ✅ Future-proof architecture
- ✅ Zero breaking changes
- ✅ Schema preserved

## Known Issues

### Deephaven `TableDataService` bug (fixed)

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

## Next Steps

The system is production-ready. Potential future improvements:
1. Performance monitoring/metrics
2. Additional validation tests
3. Documentation updates as needed
4. Explore Deephaven WAL fix when available

## Status Summary

🟢 **All Systems Operational**

- Data flowing correctly
- Schema preserved
- Zero breaking changes
- Code simplified and maintainable
- Comprehensive test coverage
- Full documentation
