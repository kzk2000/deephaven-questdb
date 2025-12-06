# Materialized View for Orderbook Storage Optimization

## Overview

The `orderbooks_latest_1s` materialized view automatically keeps only the latest orderbook snapshot per second, significantly reducing storage requirements while maintaining high temporal resolution.

## Implementation

### Creating the View

```sql
CREATE MATERIALIZED VIEW orderbooks_latest_1s AS 
SELECT 
    timestamp, 
    exchange, 
    symbol, 
    last(bids) AS bids, 
    last(asks) AS asks 
FROM orderbooks_compact 
SAMPLE BY 1s
```

This materialized view:
- **Samples** orderbook snapshots to 1-second intervals
- **Keeps** only the last (most recent) snapshot within each second
- **Updates** automatically as new data flows into `orderbooks_compact`
- **Reduces** storage by ~97% (30x compression ratio)

### Schema

| Column | Type | Description |
|--------|------|-------------|
| `timestamp` | TIMESTAMP | Second-resolution timestamp (designated) |
| `exchange` | SYMBOL | Exchange identifier (indexed) |
| `symbol` | SYMBOL | Trading pair symbol (indexed) |
| `bids` | VARCHAR | JSON array of [price, size] bid levels |
| `asks` | VARCHAR | JSON array of [price, size] ask levels |

## Performance

### Storage Reduction

Based on BTC-USD feeds from 3 exchanges (COINBASE, KRAKEN, BITSTAMP):

```
Base table (orderbooks_compact):  ~40,000 rows (all snapshots)
Materialized view (latest 1s):    ~1,300 rows (1 per second)

Storage reduction: 96.7%
Compression ratio: 30.5x
```

### Query Performance

Querying the materialized view is significantly faster because:
1. **30x fewer rows** to scan
2. **Pre-aggregated** data (no `SAMPLE BY` computation needed)
3. **Cached results** persisted to disk

Example query times:
- Full table scan on base: 10-100ms
- Materialized view query: 1-5ms (10-100x faster)

## Usage Examples

### Latest Orderbook Per Exchange

```sql
SELECT * FROM orderbooks_latest_1s 
WHERE symbol = 'BTC-USD' 
ORDER BY timestamp DESC 
LIMIT 3;
```

### Last Hour of Data (1-second resolution)

```sql
SELECT * FROM orderbooks_latest_1s 
WHERE symbol = 'BTC-USD' 
  AND timestamp > dateadd('h', -1, now())
ORDER BY timestamp DESC;
```

### Best Bid/Ask Over Time

```sql
SELECT 
    timestamp,
    exchange,
    symbol,
    json_extract(bids, '$[0][0]') AS best_bid_price,
    json_extract(asks, '$[0][0]') AS best_ask_price
FROM orderbooks_latest_1s
WHERE symbol = 'BTC-USD'
  AND timestamp > dateadd('m', -5, now())
ORDER BY timestamp;
```

## Monitoring

### Check View Status

```sql
SELECT 
    view_name,
    base_table_name,
    view_status,
    last_refresh_start_timestamp,
    last_refresh_finish_timestamp
FROM materialized_views()
WHERE view_name = 'orderbooks_latest_1s';
```

### Check Refresh Lag

```sql
SELECT 
    view_name,
    refresh_base_table_txn,
    base_table_txn,
    (base_table_txn - refresh_base_table_txn) AS lag_transactions
FROM materialized_views()
WHERE view_name = 'orderbooks_latest_1s';
```

When `lag_transactions = 0`, the view is fully up-to-date.

## Data Flow

```
Cryptofeed → orderbooks_compact → orderbooks_latest_1s
             (all snapshots)       (1 per second)
                                          ↓
                                   Queries & Analytics
```

### Refresh Mechanism

The materialized view uses `REFRESH IMMEDIATE` strategy (default):
- **Automatic**: Refreshes when new data is inserted into `orderbooks_compact`
- **Incremental**: Only processes new time ranges (not full table scan)
- **Asynchronous**: Minimal impact on write performance
- **Real-time**: Typically <1 second latency

## Configuration Options

### Alternative Sampling Intervals

**10-second resolution** (even more storage reduction):
```sql
CREATE MATERIALIZED VIEW orderbooks_latest_10s AS 
SELECT timestamp, exchange, symbol, last(bids) AS bids, last(asks) AS asks 
FROM orderbooks_compact 
SAMPLE BY 10s;
```

**100-millisecond resolution** (high-frequency trading):
```sql
CREATE MATERIALIZED VIEW orderbooks_latest_100ms AS 
SELECT timestamp, exchange, symbol, last(bids) AS bids, last(asks) AS asks 
FROM orderbooks_compact 
SAMPLE BY 100ms;
```

### TTL (Time-to-Live)

Automatically expire old data:
```sql
CREATE MATERIALIZED VIEW orderbooks_latest_1s AS 
SELECT timestamp, exchange, symbol, last(bids) AS bids, last(asks) AS asks 
FROM orderbooks_compact 
SAMPLE BY 1s
PARTITION BY DAY TTL 7 DAYS;  -- Keep last 7 days
```

### Manual Refresh

If you prefer manual control:
```sql
CREATE MATERIALIZED VIEW orderbooks_latest_1s 
REFRESH MANUAL AS 
SELECT timestamp, exchange, symbol, last(bids) AS bids, last(asks) AS asks 
FROM orderbooks_compact 
SAMPLE BY 1s;

-- Refresh manually:
REFRESH MATERIALIZED VIEW orderbooks_latest_1s;
```

## Maintenance

### Drop and Recreate

```sql
-- Drop the view
DROP MATERIALIZED VIEW orderbooks_latest_1s;

-- Recreate with new configuration
CREATE MATERIALIZED VIEW orderbooks_latest_1s AS ...
```

### Full Refresh

If the view becomes invalid:
```sql
REFRESH MATERIALIZED VIEW orderbooks_latest_1s FULL;
```

## Best Practices

1. **Query the view, not the base table** for time-series analytics
2. **Use appropriate sampling intervals** (1s is good for most use cases)
3. **Monitor refresh lag** to ensure view stays current
4. **Set TTL** if you don't need infinite history
5. **Test query performance** before moving to production

## Integration with Deephaven

Access the materialized view from Deephaven notebooks:

```python
from dhquest.qdb import get_qdb_connection

# Get connection
conn = get_qdb_connection()

# Query materialized view
orderbooks_1s = conn.cursor().execute("""
    SELECT * FROM orderbooks_latest_1s 
    WHERE symbol = 'BTC-USD' 
      AND timestamp > dateadd('h', -1, now())
    ORDER BY timestamp
""").fetchall()

# Convert to Deephaven table for analysis
# ...
```

## References

- [QuestDB Materialized Views Guide](https://questdb.com/docs/guides/mat-views/)
- [CREATE MATERIALIZED VIEW Syntax](https://questdb.com/docs/reference/sql/create-mat-view/)
- [Compact Orderbook Usage](./COMPACT_ORDERBOOK_USAGE.md)
- [Writer Comparison](./WRITER_COMPARISON.md)
