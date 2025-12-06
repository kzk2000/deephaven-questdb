# Compact Orderbook Storage

## Overview

The `write_orderbook_compact()` method stores orderbook data in JSON array format instead of expanding into individual columns. This provides flexible storage that can handle variable-depth orderbooks without schema changes.

## Schema Comparison

### Expanded Format (`orderbooks` table)
```
Columns: exchange, symbol, bid_0_price, bid_0_size, ..., bid_9_price, bid_9_size,
         ask_0_price, ask_0_size, ..., ask_9_price, ask_9_size, timestamp
Total: 43 columns for 10 levels
```

### Compact Format (`orderbooks_compact` table)
```
Columns: exchange, symbol, bids, asks, timestamp
Total: 5 columns (any depth)
```

**Data Format:**
- `bids`: JSON array of `[price, size]` pairs (e.g., `[[89500.0, 1.5], [89499.0, 2.0], ...]`)
- `asks`: JSON array of `[price, size]` pairs (e.g., `[[89501.0, 1.4], [89502.0, 2.0], ...]`)

## Usage

### Simple Writer

```python
from src.questdb_writer import QuestDBWriter

writer = QuestDBWriter(host='questdb', port=9009)

# Write with default 20 levels
writer.write_orderbook_compact(book, receipt_timestamp)

# Write with custom depth
writer.write_orderbook_compact(book, receipt_timestamp, depth=50)

# Write all available levels
writer.write_orderbook_compact(book, receipt_timestamp, depth=None)
```

### Queued Writer (High Throughput)

```python
from src.questdb_writer_queued import QuestDBWriterQueued

writer = QuestDBWriterQueued(host='questdb', port=9009)
await writer.start()

# Async writes
await writer.write_orderbook_compact(book, receipt_timestamp, depth=20)
```

### Example Script

See `cryptofeed_3_orderbooks_compact.py` for a complete example:

```python
async def write_to_questdb_compact(book, receipt_timestamp):
    questdb_writer.write_orderbook_compact(book, receipt_timestamp, depth=20)

callbacks = {L2_BOOK: [write_to_questdb_compact, cft.my_print]}
```

## Querying Compact Data

### Get Latest Snapshot

```sql
SELECT exchange, symbol, bids, asks, timestamp
FROM orderbooks_compact
WHERE exchange = 'COINBASE' AND symbol = 'BTC-USD'
ORDER BY timestamp DESC
LIMIT 1;
```

### Extract Top-of-Book (Best Bid/Ask)

QuestDB doesn't have native JSON parsing yet, but you can extract in your application:

```python
import json

# Fetch row from QuestDB
result = query("SELECT bids, asks FROM orderbooks_compact LIMIT 1")
bids = json.loads(result[0]['bids'])
asks = json.loads(result[0]['asks'])

best_bid_price, best_bid_size = bids[0]
best_ask_price, best_ask_size = asks[0]

spread = best_ask_price - best_bid_price
mid_price = (best_bid_price + best_ask_price) / 2
```

### Count Snapshots by Exchange

```sql
SELECT exchange, symbol, COUNT(*) as snapshots
FROM orderbooks_compact
GROUP BY exchange, symbol
ORDER BY exchange, symbol;
```

## Benefits

### Compact Format Advantages:

1. **Flexible Depth**: Store any orderbook depth without schema changes
2. **Storage Efficiency**: ~5 columns vs 40+ columns for deep books
3. **Simple Schema**: Easier to understand and maintain
4. **Dynamic**: Can store varying depths per snapshot
5. **Full Depth**: Can capture entire orderbook (100+ levels) if needed

### Expanded Format Advantages:

1. **Columnar Analytics**: Direct SQL queries on specific price levels
2. **Fast Aggregations**: Better for computing statistics across levels
3. **Type Safety**: Prices and sizes as native DOUBLE types
4. **Indexing**: Can index specific price level columns

## When to Use Each

### Use Compact Format When:
- ✅ Need to store full orderbook depth (>10 levels)
- ✅ Orderbook depth varies across exchanges/symbols
- ✅ Building replay/backtesting systems
- ✅ Want simpler schema management
- ✅ Primarily extracting top-of-book or few levels

### Use Expanded Format When:
- ✅ Fixed depth sufficient (e.g., 10 levels)
- ✅ Need to query/aggregate specific price levels in SQL
- ✅ Performing columnar analytics on orderbook shape
- ✅ Want native numeric types for all fields

## Storage Estimates

**Example: 20-level orderbook**

Expanded format:
- 43 columns × 8 bytes (DOUBLE) = ~344 bytes + overhead

Compact format:
- 2 JSON strings × ~400 bytes = ~800 bytes
- But: Can store 50+ levels with same overhead

**Trade-off**: Compact uses more storage per row for same depth, but offers flexibility to store deeper books without schema changes.

## Configuration

### Default Depth
Both methods default to **20 levels** to balance storage and data capture:

```python
# These are equivalent
writer.write_orderbook_compact(book, receipt_timestamp)
writer.write_orderbook_compact(book, receipt_timestamp, depth=20)
```

### Custom Depth
Adjust based on your needs:

```python
# Top 5 levels only
writer.write_orderbook_compact(book, receipt_timestamp, depth=5)

# Deep liquidity analysis (100 levels)
writer.write_orderbook_compact(book, receipt_timestamp, depth=100)

# Full orderbook (all available)
writer.write_orderbook_compact(book, receipt_timestamp, depth=None)
```

## Performance

**Write Performance:**
- Similar to expanded format (~same ILP overhead)
- JSON serialization adds ~5-10% overhead
- Network bandwidth slightly higher due to JSON encoding

**Read Performance:**
- Slightly slower to parse JSON in application
- More storage to scan if reading many rows
- Excellent for single-row lookups (latest snapshot)

## Migration

Both formats can coexist:
- Use `orderbooks` (expanded) for real-time analytics
- Use `orderbooks_compact` for historical replay/backtesting
- Or choose one based on your primary use case

No migration needed - they're independent tables with different purposes.
