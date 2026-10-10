# Orderbooks

## Data format

`orderbooks` stores one snapshot per row, with the book levels as QuestDB `DOUBLE[][]` arrays:

```
bids: DOUBLE[][]  -- [[prices...], [volumes...]]
asks: DOUBLE[][]  -- [[prices...], [volumes...]]
```

Example:
```
bids: [[50000.0, 49999.0, 49998.0], [1.5, 2.0, 1.0]]
asks: [[50001.0, 50002.0, 50003.0], [1.2, 0.8, 2.5]]
```

Rows are written by `QuestDBWriter.write_orderbook` (official SDK, ILP over HTTP, numpy arrays,
top 20 levels by default).

## Retention

| Object | Partitioning | Retention |
|---|---|---|
| `orderbooks` | `PARTITION BY HOUR`, WAL | `TTL 1 HOURS` |
| `orderbooks_1s` | materialized view, `SAMPLE BY 1s` | kept (downsampled history) |

- QuestDB's smallest TTL unit is `HOURS`, and TTL must be a multiple of the partition size, so 1 hour
  is the minimum for `orderbooks`. Because whole partitions expire, expect roughly 1-2 hours of
  raw rows.
- `init_questdb_tables.py` creates the table with these settings. A table created earlier with a
  different partitioning must be dropped (together with `orderbooks_1s`) and recreated. Stop
  cryptofeed first, otherwise ILP auto-creates the table with default settings.

## Using the data

- Static, 1-second snapshots: `qdb.get_orderbooks(last_n=...)` (reads `orderbooks_1s`)
- Live, ticking: `orderbooks_live_via_questdb_wal.py` (binds `orderbooks`)

## Testing

```bash
make test        # health check and row counts
uvx --with numpy --with "questdb[dataframe]" --with pandas pytest docker/cryptofeed/tests
```

The integration and simulation tests write to throwaway `test_*` tables, never to `orderbooks`.
