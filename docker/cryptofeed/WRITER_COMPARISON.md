# QuestDB Writer Comparison

## Overview

Two implementations are available for writing to QuestDB:

1. **`questdb_writer.py`** - Simple synchronous writer
2. **`questdb_writer_queued.py`** - Async queued writer with batching

## Performance Test Results

**Raw Socket Performance:** 80,000+ writes/sec (local test)

**Current Production Load:**
- 12 feeds (3 exchanges × 4 symbols)
- ~7,860 orderbook snapshots/min (~131/sec)
- ~10-15 trades/sec
- **Total: ~145 writes/sec**
- CPU: 11.62%, Memory: 117.8 MiB
- **No errors, no backpressure**

## When to Use Each

### Use `QuestDBWriter` (Simple) When:

✅ **Low to moderate throughput** (<200 writes/sec)
✅ **Simplicity preferred** - easier to understand and debug
✅ **Current setup:** ≤20 symbols across 3 exchanges
✅ **No burst handling needed**

**Pros:**
- Simple, easy to understand
- Lower memory footprint
- Immediate writes (no buffering)
- Adequate for most use cases

**Cons:**
- Blocking writes may slow async callbacks
- No burst protection
- Could lose data on crash (no buffering)

### Use `QuestDBWriterQueued` (Async) When:

✅ **High throughput** (>500 writes/sec)
✅ **Many symbols:** >20 symbols or >60 total feeds
✅ **Bursty traffic** - need buffering
✅ **Production resilience** - don't want to lose data during brief disconnections

**Pros:**
- Non-blocking async writes
- Batching for better throughput
- Queue buffers bursts
- Handles temporary disconnections gracefully

**Cons:**
- More complex
- Higher memory usage (queue buffer)
- Slight write latency (batching delay)
- Need to monitor queue size

## Scaling Projections

| Scenario | Feeds | Est. Rate | Recommendation |
|----------|-------|-----------|----------------|
| Current | 12 | 145/sec | ✓ Simple writer |
| 10 symbols × 3 exchanges | 30 | 328/sec | ⚡ Simple OK, monitor |
| 20 symbols × 3 exchanges | 60 | 655/sec | ⚠️ Use queued writer |
| 50 symbols × 3 exchanges | 150 | 1,638/sec | ⚠️ Use queued writer |
| 20 symbols (faster snapshots) | 60 | 1,310/sec | ⚠️ Use queued writer |

## Configuration

### Simple Writer (Current)
```python
from src.questdb_writer import QuestDBWriter

questdb_writer = QuestDBWriter(host='questdb', port=9009)

# Synchronous writes
questdb_writer.write_trade(data)
questdb_writer.write_orderbook(book, receipt_timestamp, depth=10)
```

### Queued Writer
```python
from src.questdb_writer_queued import QuestDBWriterQueued

questdb_writer = QuestDBWriterQueued(
    host='questdb', 
    port=9009,
    max_queue_size=10000,  # Buffer up to 10K writes
    batch_size=100          # Write in batches of 100
)

# Start background writer task
await questdb_writer.start()

# Async writes (non-blocking)
await questdb_writer.write_trade(data)
await questdb_writer.write_orderbook(book, receipt_timestamp, depth=10)

# Monitor queue health
print(f"Queue size: {questdb_writer.get_queue_size()}")
print(f"Dropped: {questdb_writer.get_dropped_count()}")
```

## Recommendation

**For your current setup (4 symbols):** Continue using the **simple writer**. It's working perfectly with no errors.

**Switch to queued writer when:**
- You add >15 symbols (>45 feeds total)
- You reduce snapshot_interval below 50
- You see socket errors or backpressure in logs
- You need guaranteed delivery during brief outages

## Migration Path

1. **Monitor current performance:**
   - Watch for "BrokenPipeError" or "ConnectionResetError" in logs
   - Check CPU/memory usage
   - Monitor QuestDB write latency

2. **If you see issues:**
   - Switch to queued writer in `cryptofeed_2_orderbooks.py`
   - Adjust `max_queue_size` and `batch_size` based on load
   - Monitor `get_queue_size()` - if consistently >50%, increase `batch_size`

3. **Tuning parameters:**
   - `max_queue_size`: 10K for most cases, 50K for very high throughput
   - `batch_size`: 100 for balanced performance, 500+ for extreme throughput
   - Lower `batch_size` for lower latency, higher for better throughput
