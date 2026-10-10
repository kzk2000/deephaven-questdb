# deephaven-questdb

Real-time crypto analytics stack. Cryptofeed streams trades and orderbooks (Coinbase, Bitstamp, Kraken; BTC/ETH/AVAX/SOL-USD) into QuestDB. Deephaven reads QuestDB as static snapshots and as live ticking tables.

## Architecture

```
Exchanges -> cryptofeed -> QWP/WebSocket (questdb:9000) -> QuestDB 10.0.1 (WAL tables)
          -> Postgres wire (questdb:8812, admin/quest) -> Deephaven server-ui 42.6 (UI :10000)
```

- `docker-compose.yml`: services `deephaven` (-Xmx12g, anonymous auth), `questdb`, `cryptofeed` on network `quest_ntw`. Deephaven and cryptofeed wait for QuestDB's healthcheck.
- `./data/deephaven` is mounted at `/data/storage`; notebooks are at `/data/storage/notebooks` (on PYTHONPATH). `./data/questdb` is QuestDB data (gitignored).
- Schema (`docker/cryptofeed/src/init_questdb_tables.py`):
  - `trades`: TIMESTAMP_NS, SYMBOL cols, `price`, `size`, `trade_id`; PARTITION BY DAY, WAL.
  - `orderbooks`: `bids`/`asks` as `DOUBLE[][]` ([[prices],[sizes]]); PARTITION BY HOUR, WAL, TTL 1 HOURS (QuestDB's minimum TTL unit is HOUR and TTL must be a multiple of the partition size; the view `orderbooks_1s` keeps the downsampled history). An existing DAY-partitioned table must be dropped (with `orderbooks_1s`) and recreated; stop cryptofeed first or ILP auto-creates it with defaults.
  - Materialized view `orderbooks_1s` (SAMPLE BY 1s).

## Key files

- `Makefile`: build, build_with_drop, up, down, rebuild, restart, clean, ps, logs*, test, urls, format.
- `pyproject.toml`: optional dep groups `cryptofeed`, `deephaven`. Ruff: line length 100, py310, double quotes. Pytest testpaths: `docker/cryptofeed/tests`.
- `docker/cryptofeed/Dockerfile`: python:3.13 (cryptofeed 3.x requires it); overwrites cryptofeed's `exchanges/coinbase.py` with `src/coinbase.py`, the no-auth hack: it keeps using the public Coinbase Exchange feed (`wss://ws-feed.exchange.coinbase.com`: `matches`, `level2_batch`, `ticker`) instead of upstream's Advanced Trade client. It is ported to cryptofeed 3.0.1 and the build fails if another version is installed; on a cryptofeed upgrade re-port it (diff against the new upstream `coinbase.py`).
- `docker/cryptofeed/src/script/cryptofeed_0_startup.sh`: init tables, sleep 2s, launch trades/orderbooks feeds, restart on death.
- `src/questdb_writer.py`: `QuestDBWriter` (questdb SDK; QWP over WebSocket by default, `protocol="http"`/`QUESTDB_PROTOCOL=http` for ILP/HTTP). Rows are appended per tick and published by a 25 ms background flush: row() never blocks, rows are visible ~20-70 ms after the tick (ILP/HTTP defaults: ~560 ms median) and a multi-second server outage is buffered and replayed without loss. Server rejections arrive asynchronously (`writer.rejections`, logged); do not call `flush(wait=True)` per tick (~10 ms each). `write_trade`, `write_orderbook` (2D numpy), `execute_sql` (REST). `questdb_writer_queued.py` is mostly commented out.
- `docker/deephaven/Dockerfile`: pip installs connectorx, sqlalchemy, psycopg2, polars and `deephaven-plugin-tradingview-lightweight==0.0.1` (TradingView charts; alpha). The old `TableDataService` callback patch was removed (fixed upstream in 42.6; see `docs/DEEPHAVEN_BUG_REPORT.md`).
- `data/deephaven/notebooks/qdb.py`: static access via ConnectorX (`redshift://...:8812`). `run_query`, `to_table()` (timestamp[us] -> timestamp[ns, tz=UTC], large_list -> list), `get_trades`, `get_candles`, `get_orderbooks`, `get_engine()` (SQLAlchemy, for live).
- `data/deephaven/notebooks/qdb_qwp.py` + `qdb_backend.py` `transport="qwp"` (opt-in, PoC): reads over QuestDB's WebSocket protocol (QWP) as Arrow, paged with `ORDER BY ts LIMIT lo,hi` instead of `ROW_NUMBER()` over the Postgres wire. On a 1M-row table a 64k page is ~3-6 ms vs ~90-120 ms; live-table freshness is unchanged (QWP cannot push, so the backend still polls, now with `count()`). `DOUBLE[][]` columns arrive as real `double[][]` (usable in formulas, e.g. `bids[0][0]`): `TableDataService` infers `double[]` from a nested-list schema, so `qdb_qwp.schema()` reads the true dimensions from `table_columns()` and adds a `deephaven:type="double[][]"` field hint (a zero-row QWP result cannot tell `DOUBLE[]` from `DOUBLE[][]`). `QwpReader(flatten_arrays=True)` gives JSON strings instead. Enable with `TRANSPORT = "qwp"` before running a live notebook, or `create_live_table(..., transport="qwp")`. Default stays `pg`.
- `notebooks/qdb_backend.py`: `QuestDBBackend(TableDataServiceBackend)`. One backend per table in a module-global registry (re-runnable notebooks). Polls `wal_transactions()` every 50ms, falls back to `count(*)`; pages with `ROW_NUMBER() OVER (ORDER BY timestamp)`. API: `create_live_table`, `set_verbose`, `stop_monitoring`.
- Notebooks: static `trades_static.py` (binds `trades_static`), `orderbooks_static.py` (binds `orderbooks_static`); live notebooks bind `trades` / `orderbooks`; live `trades_live_via_questdb_wal.py`, `orderbooks_live_via_questdb_wal.py` (add `latency_seconds`); charts `trades_chart_live.py` (TradingView candles + volume, tick lines; `tvl` plugin 0.0.1 has no `tvl.legend`; candles are binned with `agg_by`, because plugin `auto_bin` produced a 0-based price axis), `trades_chart_simple.py`.
- `docs/` status, orderbooks, bug report, `history/`; `docker/cryptofeed/tests/` unit/integration/simulation + `utils/` (status scripts) + `run_tests.sh`.

## How to run

- First run: `make rebuild`, wait ~30s, `make test`. Deephaven http://localhost:10000, QuestDB http://localhost:9000.
- Wrong timestamp type on tables: `make build_with_drop`.
- Format: `make format` (`uvx ruff format`). Tests: pytest via uvx.

## Gotchas

- **DH editor overwrites files**: closing a notebook in the Deephaven UI writes its (possibly stale) editor buffer back to `data/deephaven/notebooks/`. Close files in the UI before editing them from outside, and re-check `git diff` afterwards.
- **Tests are isolated**: `docker/cryptofeed/tests` write to throwaway `test_*` tables (dropped on teardown), never to live `trades` / `orderbooks`. Run with `make pytest`.

- **Timestamp precision**: tables must be TIMESTAMP_NS. If ILP writes beat the DDL, QuestDB auto-creates microsecond tables (fixed in 7b0bf73 with IF NOT EXISTS, 2s sleep, schema check). Deephaven rejects MICROSECOND units, so static loads must go through `qdb.to_table()`.
- **Two connection paths**: static notebooks use ConnectorX (`get_connection()` returns a string); live notebooks use SQLAlchemy via `get_engine()`. Mixing them gives `'str' has no attribute 'connect'`.
- **Column naming**: cryptofeed's `amount` is stored as `size`.
- **Perpetual futures** (`src/perps.py`, `src/script/cryptofeed_3_perps.py`, a third supervised process): BTC and ETH perps, linear (`BTC-USDT-PERP`, `BTC-USDC-PERP`) and inverse (`BTC-USD-PERP`), from Deribit, OKX, Binance (`BINANCE_FUTURES` USDT-M, `BINANCE_DELIVERY` COIN-M) and Bybit. They share the spot `trades` / `orderbooks` tables. `size` is always base coin; `trades` also has `size_native` + `size_unit` (BASE / USD / CONTRACTS), `contract_size` (1 for spot; coin or USD per contract), `contract_type` (spot / linear / inverse) and `notional_usd`, so `SUM(notional_usd)` is valid across every row. Inverse size = USD notional / price. Venues differ: Deribit inverse is USD, OKX is contracts (`ctVal` read from its instrument list), Binance COIN-M is contracts (`contractSize`), Bybit inverse is USD.
- **Binance and Bybit are region restricted**: from this host `fapi/dapi.binance.com` return HTTP 451 and `api.bybit.com` 403, so only Deribit and OKX stream here. The perps process probes every venue at start, skips unreachable ones with the reason in the log, and re-probes every `PERP_REPROBE_SECONDS` (600), restarting itself when one becomes reachable. `PERP_VENUES=deribit,okx` restricts the set. The Binance/Bybit unit conversions follow vendor docs and are unit-tested against sample payloads but have not been verified live.
- **Backfilling the contract columns** after adding them to an old `trades` table (one time): `UPDATE trades SET size_native = size, size_unit = 'BASE', contract_size = 1.0, contract_type = 'spot', notional_usd = price * size WHERE contract_size IS NULL`.
- **trades is deduplicated** (`DEDUP UPSERT KEYS(timestamp, exchange, symbol, trade_id)`, set by `init_questdb_tables.py`): a replayed trade (Coinbase re-sends `last_match` on every subscribe; restarts; QWP reconnect replay) overwrites itself. `trade_id` must stay in the key (thousands of timestamps hold several distinct trades) and must never be NULL: NULL ids collapse distinct trades, so the writer substitutes a unique `rx-...` id. Rows stored before DEDUP was enabled are not rewritten. Stored timestamps are `TIMESTAMP_NS` but the sources only give microseconds; sub-microsecond digits are float rounding noise.
- **Backend performance**: each `column_values` call runs `ROW_NUMBER()` over the whole table and fetches the schema twice; slow on large tables.
