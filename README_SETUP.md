# Setup Instructions

## Quick Start

For a clean setup, run:

```bash
# Clone the repository
git clone <your-repo-url>
cd deephaven-questdb

# Build and start all services
docker-compose build --no-cache
docker-compose up -d

# Wait for services to be healthy (about 30 seconds)
docker-compose ps

# Access the UIs
# - Deephaven: http://localhost:10000
# - QuestDB:   http://localhost:9000
# - Redpanda:  http://localhost:8080
```

## If You Encounter Issues

If `docker-compose up -d` fails with ContainerConfig errors or stale containers:

```bash
# Clean everything and start fresh
docker-compose down -v
docker-compose up -d
```

The `-v` flag removes volumes, ensuring a completely clean start.

## Services

| Service | Port | Description |
|---------|------|-------------|
| Deephaven | 10000 | Real-time analytics UI |
| QuestDB | 9000 (HTTP), 8812 (PostgreSQL), 9009 (ILP) | Time-series database |
| Redpanda | 9092, 29092 | Kafka-compatible message broker |
| Redpanda Console | 8080 | Kafka UI |
| Cryptofeed | - | Cryptocurrency data streamer |

## Data Directories

The following directories are created automatically:

- `data/questdb/` - QuestDB database files (gitignored)
- `data/deephaven/` - Deephaven notebooks and configuration
- `data/parquet/` - Parquet file storage

**Note**: `data/questdb/` is excluded from git as it contains runtime database files.

## Verifying Setup

Check all services are healthy:

```bash
docker-compose ps
```

All services should show `Up (healthy)` or `Up` status.

View logs for any service:

```bash
docker-compose logs -f <service-name>
# Examples:
docker-compose logs -f questdb
docker-compose logs -f deephaven_qdb
docker-compose logs -f cryptofeed
```

## Stopping Services

```bash
# Stop all services
docker-compose down

# Stop and remove volumes (clean slate)
docker-compose down -v
```

## Known Issues

### ContainerConfig Error on Rebuild

**Symptom**: After `docker-compose build --no-cache`, running `docker-compose up -d` fails with:
```
ERROR: for questdb  'ContainerConfig'
```

**Cause**: Docker Swarm mode + stale containers from previous runs

**Solution**:
```bash
docker-compose down
docker-compose up -d
```

### TableDataService API Not Working

**Status**: Known bug in Deephaven edge build (as of Dec 2024)

The TableDataService API for WAL-driven streaming has a callback bug. See `DEEPHAVEN_BUG_REPORT.md` for details.

**Workaround**: Use `qdb.py` with polling:
```python
import qdb
trades = qdb.get_trades(last_nticks=10000)
```

## Architecture

```
Cryptofeed (Coinbase/Kraken)
    ↓ (WebSocket)
QuestDB (Time-series DB)
    ↓ (PostgreSQL wire protocol)
Deephaven (Real-time Analytics)
    ↓
Browser UI (http://localhost:10000)
```

Data flows:
1. Cryptofeed subscribes to exchange WebSocket feeds
2. Writes to QuestDB via ILP (Influx Line Protocol)
3. QuestDB stores in columnar format with WAL
4. Deephaven queries via PostgreSQL protocol
5. Real-time visualization in browser

## Next Steps

1. **Access Deephaven UI**: http://localhost:10000
2. **Test QuestDB connection**:
   ```python
   exec(open('/data/notebooks/test_tds_minimal.py').read())
   ```
3. **Query trades**:
   ```python
   import qdb
   trades = qdb.get_trades(last_nticks=1000)
   ```

## Troubleshooting

### Cryptofeed Connection Errors

If you see `Failed to connect to QuestDB`:
- Wait 30 seconds for QuestDB to fully start
- Check QuestDB health: `docker-compose ps questdb`
- Verify port 9009 is open: `docker-compose logs questdb | grep 9009`

### Deephaven Not Starting

Check logs:
```bash
docker-compose logs deephaven_qdb
```

Common issues:
- Not enough memory (needs 12GB heap)
- QuestDB not healthy yet (check dependencies)

### Port Conflicts

If ports are already in use, edit `docker-compose.yml` and change the external port mapping:
```yaml
ports:
  - "10001:10000"  # Change left side only
```

## Development

To make changes:

1. **Edit code** in `docker/cryptofeed/` or `data/deephaven/notebooks/`
2. **Rebuild specific service**:
   ```bash
   docker-compose build cryptofeed
   docker-compose up -d cryptofeed
   ```
3. **View logs**:
   ```bash
   docker-compose logs -f cryptofeed
   ```

## Resources

- [Deephaven Documentation](https://deephaven.io/core/docs/)
- [QuestDB Documentation](https://questdb.io/docs/)
- [Cryptofeed Documentation](https://github.com/bmoscon/cryptofeed)
