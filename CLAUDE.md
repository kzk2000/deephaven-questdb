# deephaven-questdb

Real-time crypto analytics stack. Cryptofeed streams trades and orderbooks (Coinbase, Bitstamp, Kraken; BTC/ETH/AVAX/SOL-USD) into QuestDB. Deephaven reads QuestDB as static snapshots and as live ticking tables.

## Architecture

```
Exchanges -> cryptofeed -> ILP/HTTP (questdb:9000) -> QuestDB 10.0.1 (WAL tables)
          -> Postgres wire (questdb:8812, admin/quest) -> Deephaven server-ui 42.6 (UI :10000)
```

- `docker-compose.yml`: services `deephaven` (-Xmx12g, anonymous auth), `questdb`, `cryptofeed` on network `quest_ntw`. Deephaven and cryptofeed wait for QuestDB's healthcheck.
- `./data/deephaven` is mounted at `/data/storage`; notebooks are at `/data/storage/notebooks` (on PYTHONPATH). `./data/questdb` is QuestDB data (gitignored).
- Schema (`docker/cryptofeed/src/init_questdb_tables.py`):
  - `trades`: TIMESTAMP_NS, SYMBOL cols, `price`, `size`, `trade_id`; PARTITION BY DAY, WAL.
  - `orderbooks`: `bids`/`asks` as `DOUBLE[][]` ([[prices],[sizes]]); PARTITION BY HOUR, WAL, 1h TTL.
  - Materialized view `orderbooks_1s` (SAMPLE BY 1s).

## Key files

- `Makefile`: build, build_with_drop, up, down, rebuild, restart, clean, ps, logs*, test, urls, format.
- `pyproject.toml`: optional dep groups `cryptofeed`, `deephaven`. Ruff: line length 100, py310, double quotes. Pytest testpaths: `docker/cryptofeed/tests`.
- `docker/cryptofeed/Dockerfile`: python:3.12; overwrites cryptofeed's `coinbase.py` with `src/coinbase.py` (uses undocumented Coinbase endpoints to avoid auth).
- `docker/cryptofeed/src/script/cryptofeed_0_startup.sh`: init tables, sleep 2s, launch trades/orderbooks feeds, restart on death.
- `src/questdb_writer.py`: `QuestDBWriter` (questdb SDK `Sender`, HTTP ILP). `write_trade`, `write_orderbook` (2D numpy), `execute_sql` (REST). `questdb_writer_queued.py` is mostly commented out.
- `docker/deephaven/Dockerfile`: pip installs connectorx, sqlalchemy, psycopg2, polars. The old `TableDataService` callback patch was removed (fixed upstream in 42.6; see `docs/DEEPHAVEN_BUG_REPORT.md`).
- `data/deephaven/notebooks/qdb.py`: static access via ConnectorX (`redshift://...:8812`). `run_query`, `to_table()` (timestamp[us] -> timestamp[ns, tz=UTC], large_list -> list), `get_trades`, `get_candles`, `get_orderbooks`, `get_engine()` (SQLAlchemy, for live).
- `notebooks/qdb_backend.py`: `QuestDBBackend(TableDataServiceBackend)`. One backend per table in a module-global registry (re-runnable notebooks). Polls `wal_transactions()` every 50ms, falls back to `count(*)`; pages with `ROW_NUMBER() OVER (ORDER BY timestamp)`. API: `create_live_table`, `set_verbose`, `stop_monitoring`.
- Notebooks: static `trades_static.py`, `orderbooks_static.py`; live `trades_live_via_questdb_wal.py`, `orderbooks_live_via_questdb_wal.py` (add `latency_seconds`); charts `trades_chart_live.py`, `trades_chart_simple.py`.
- `validation/` ad-hoc checks; `docs/` status, orderbooks, bug report, `history/`; `docker/cryptofeed/tests/` unit/integration/simulation + `run_tests.sh`.

## How to run

- First run: `make rebuild`, wait ~30s, `make test`. Deephaven http://localhost:10000, QuestDB http://localhost:9000.
- Wrong timestamp type on tables: `make build_with_drop`.
- Format: `make format` (`uvx ruff format`). Tests: pytest via uvx.

## Gotchas

- **Timestamp precision**: tables must be TIMESTAMP_NS. If ILP writes beat the DDL, QuestDB auto-creates microsecond tables (fixed in 7b0bf73 with IF NOT EXISTS, 2s sleep, schema check). Deephaven rejects MICROSECOND units, so static loads must go through `qdb.to_table()`.
- **Two connection paths**: static notebooks use ConnectorX (`get_connection()` returns a string); live notebooks use SQLAlchemy via `get_engine()`. Mixing them gives `'str' has no attribute 'connect'`.
- **Column naming**: cryptofeed's `amount` is stored as `size`.
- **Known Makefile bugs**: `logs-dh` uses service `deephaven_qdb` (should be `deephaven`); `clean` uses bash `[[ ]]` under `/bin/sh`; `urls` lists ILP on 9009 but writer uses HTTP on 9000.
- **Stale README**: references nonexistent `test_tds_minimal.py`; real path is `/data/storage/notebooks`.
- **`qdb.get_orderbooks(last_n)`** ignores its argument (hard-coded `LIMIT -10`); docstring is inaccurate.
- **Backend performance**: each `column_values` call runs `ROW_NUMBER()` over the whole table and fetches the schema twice; slow on large tables.
- **Leftover config**: app-mode configs (`data/deephaven/app.d/config.app`, `docker/deephaven/config/app.d/cryptofeed.app`, `init_app.py`) have all script entries commented out.
