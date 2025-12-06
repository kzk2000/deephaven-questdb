# QuestDB TTL (Time-To-Live) Configuration

## Overview

Time-To-Live (TTL) automatically removes old partitions as new data is ingested, providing efficient data retention management without manual intervention.

## Current Configuration

| Table | Partition | TTL | Purpose |
|-------|-----------|-----|---------|
| `trades` | DAY | None | Permanent trade history |
| `orderbooks` | HOUR | **1 HOUR** | Expanded orderbook format (optional) |
| `orderbooks_compact` | HOUR | **1 HOUR** | High-frequency snapshots (auto-expire) |
| `orderbooks_latest_1s` | DAY | **None** | Materialized view (1-second sampling, permanent) |

## Why TTL on orderbooks_compact?

The `orderbooks_compact` table receives **very high-frequency** orderbook snapshots:
- **~30-100 updates per second** per exchange/symbol
- **~2-3 million rows per day** for 3 exchanges
- **Raw, high-resolution** data (millisecond precision)

With **1 HOUR TTL** and **HOUR partitioning**:
- Keep last 1 hour of detailed snapshots for real-time analysis
- Automatically drop old partitions hourly (no manual cleanup)
- Historical aggregated data preserved in `orderbooks_latest_1s` materialized view (permanent)

## Architecture

```
orderbooks_compact (base table)
├─ TTL: 1 HOUR
├─ High-frequency snapshots (100+ per second)
├─ Partitioned by HOUR
└─ Auto-expires partitions older than 1 hour
      ↓
orderbooks_latest_1s (materialized view)
├─ TTL: None (permanent)
├─ 1 snapshot per second (100x reduction)
├─ Partitioned by DAY (default for 1s sampling)
└─ Keeps historical aggregated data indefinitely
```

## Automatic Initialization

TTL is configured automatically at container startup via `init_questdb_tables.py`:

```python
# Run before cryptofeed starts
python /cryptofeed/src/init_questdb_tables.py questdb
```

This script:
1. Waits for QuestDB to be ready
2. Checks if tables exist
3. Applies TTL settings to `orderbooks_compact`
4. Creates materialized view if needed
5. Reports configuration status

## Manual TTL Configuration

### Add/Modify TTL

```sql
-- Current: 1-hour TTL (table partitioned by HOUR)
ALTER TABLE orderbooks_compact SET TTL 1 HOURS;

-- Set 2-hour TTL (must be integer multiple of partition)
ALTER TABLE orderbooks_compact SET TTL 2 HOURS;

-- Set 1-day TTL (ERROR: table partitioned by HOUR)
-- Must use HOURS for HOUR-partitioned tables
ALTER TABLE orderbooks_compact SET TTL 1 DAYS;  -- ❌ FAILS
```

### Remove TTL

```sql
-- Drop TTL (keep data indefinitely)
ALTER TABLE orderbooks_compact DROP TTL;
```

### Check Current TTL

```sql
SELECT name, partitionBy, ttl 
FROM tables() 
WHERE name = 'orderbooks_compact';
```

## TTL Requirements

1. **Partition Alignment**: TTL must be an integer multiple of partition size
   - Table partitioned by `DAY` → TTL in whole days
   - Table partitioned by `HOUR` → TTL in whole hours
   - Table partitioned by `MONTH` → TTL in whole months

2. **Partition-Level Deletion**: QuestDB drops entire partitions
   - Cannot delete individual rows
   - Data removed when partition becomes older than TTL
   - Most recent partition never deleted

3. **Independent TTL**: Base table and materialized view TTL are independent
   - `orderbooks_compact` has 1 DAY TTL
   - `orderbooks_latest_1s` has no TTL (permanent)

## Storage Estimation

**Without TTL** (30 days):
```
orderbooks_compact: 2M rows/day × 30 days = 60M rows
Storage: ~15-20 GB
```

**With 1 DAY TTL**:
```
orderbooks_compact: 2M rows/day × 1 day = 2M rows
Storage: ~500 MB - 1 GB
```

**Materialized View** (permanent):
```
orderbooks_latest_1s: ~70K rows/day × 30 days = 2.1M rows
Storage: ~400-500 MB (30 days)
```

**Total with TTL**: ~1-2 GB (vs 15-20 GB without)

## Best Practices

### 1. Raw Data with Short TTL

```sql
-- High-frequency base table: short TTL
ALTER TABLE orderbooks_compact SET TTL 1 DAYS;
```

### 2. Aggregated Data with Long/No TTL

```sql
-- Materialized view: keep historical aggregates
CREATE MATERIALIZED VIEW orderbooks_latest_1s AS 
SELECT timestamp, exchange, symbol, last(bids) AS bids, last(asks) AS asks 
FROM orderbooks_compact 
SAMPLE BY 1s;
-- No TTL on view = permanent storage
```

### 3. Monitor Partition Deletion

```sql
-- Check active partitions
SELECT * FROM table_partitions('orderbooks_compact');

-- View oldest/newest data
SELECT MIN(timestamp), MAX(timestamp) 
FROM orderbooks_compact;
```

### 4. Adjust TTL Based on Usage

| Use Case | Recommended TTL | Reason |
|----------|----------------|---------|
| Real-time dashboards | 1-3 DAYS | Recent data sufficient |
| Backtesting | 7-30 DAYS | Need more history |
| Long-term analysis | No TTL | Use materialized view |
| Development/Testing | 1 DAY | Minimize storage |

## Configuration Files

### Docker Startup

`docker/cryptofeed/src/script/cryptofeed_0_startup.sh`:
```bash
# Initialize QuestDB tables with TTL
python /cryptofeed/src/init_questdb_tables.py questdb

# Then start data feeds
python /cryptofeed/src/script/cryptofeed_1_trades.py &
python /cryptofeed/src/script/cryptofeed_3_orderbooks_compact.py &
```

### Initialization Script

`docker/cryptofeed/src/init_questdb_tables.py`:
```python
tables_config = {
    'orderbooks_compact': {
        'ttl': '1 HOURS',
        'partition': 'HOUR',
    }
}
```

To modify default TTL, edit the `tables_config` dictionary.

## Docker Compose Integration

The TTL initialization runs automatically when the cryptofeed container starts:

```yaml
# docker-compose.yml
services:
  cryptofeed:
    depends_on:
      questdb:
        condition: service_healthy
    # Container startup:
    # 1. Wait for QuestDB
    # 2. Run init_questdb_tables.py (set TTL)
    # 3. Start cryptofeed feeds
```

## Monitoring

### Check TTL Status

```bash
# Via curl
curl -G "http://localhost:9000/exec" \
  --data-urlencode "query=SELECT name, partitionBy, ttl FROM tables() WHERE name = 'orderbooks_compact'"
```

### Monitor Partition Deletion

```sql
-- Active partitions
SELECT name, maxTimestamp 
FROM table_partitions('orderbooks_compact') 
ORDER BY maxTimestamp DESC;

-- Partition count (should stay at ~1-2 with 1 DAY TTL)
SELECT COUNT(*) as partition_count 
FROM table_partitions('orderbooks_compact');
```

### Row Count Over Time

```sql
-- Total rows (should stabilize around 2M with 1 DAY TTL)
SELECT COUNT(*) FROM orderbooks_compact;

-- Oldest data timestamp (should be ~24 hours ago)
SELECT MIN(timestamp) as oldest_data FROM orderbooks_compact;
```

## Troubleshooting

### TTL Not Working

**Problem**: Old data not being deleted

**Solutions**:
1. Check TTL is set:
   ```sql
   SELECT name, ttl FROM tables() WHERE name = 'orderbooks_compact';
   ```

2. Verify partition boundaries:
   ```sql
   SELECT * FROM table_partitions('orderbooks_compact');
   ```

3. TTL applies at partition level - data deleted when entire partition expires

### Wrong TTL Unit

**Problem**: `TTL value must be an integer multiple of partition size`

**Solution**: Match TTL to partition:
```sql
-- Table partitioned by DAY
ALTER TABLE orderbooks_compact SET TTL 1 DAYS;  -- ✅ OK
ALTER TABLE orderbooks_compact SET TTL 12 HOURS; -- ❌ FAIL

-- Table partitioned by HOUR
ALTER TABLE hourly_data SET TTL 24 HOURS;  -- ✅ OK
ALTER TABLE hourly_data SET TTL 1 DAYS;    -- ❌ FAIL (use HOURS)
```

### Initialization Fails on Startup

**Problem**: Container fails to start

**Check logs**:
```bash
docker logs cryptofeed
```

**Common issues**:
1. QuestDB not ready → Increase `max_retries` in `init_questdb_tables.py`
2. Network issues → Check `docker-compose.yml` dependencies
3. Table already exists → Safe to ignore (script handles this)

## References

- [QuestDB TTL Documentation](https://questdb.com/docs/concept/ttl/)
- [ALTER TABLE SET TTL](https://questdb.com/docs/reference/sql/alter-table-set-ttl/)
- [Data Retention Guide](https://questdb.com/docs/operations/data-retention/)
- [Materialized Views with TTL](MATERIALIZED_VIEW.md)
- [Compact Orderbook Format](COMPACT_ORDERBOOK_USAGE.md)
