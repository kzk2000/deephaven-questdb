"""
Initialize QuestDB tables with proper TTL settings at startup.
This script should be run before starting cryptofeed to ensure tables exist with correct configuration.
"""

import sys
import time

import perps

# Idempotent trades: a replayed trade (Coinbase re-sends its last match on every subscribe, a
# restart or a QWP reconnect replay) overwrites itself instead of adding a row. The trade id
# must be part of the key: thousands of timestamps hold several distinct trades. A NULL id
# would collapse distinct trades, so the writer always sends one (see questdb_writer.py).
TRADES_DEDUP_KEYS = ("timestamp", "exchange", "symbol", "trade_id")
TRADES_DEDUP_SQL = f"ALTER TABLE trades DEDUP ENABLE UPSERT KEYS({', '.join(TRADES_DEDUP_KEYS)})"

# Columns that make spot and derivatives comparable. `size` is always base coin; these keep what
# the venue sent and how to convert it (see perps.py). Spot rows: contract_size 1, size_unit BASE,
# contract_type spot, notional_usd = price x size.
TRADES_EXTRA_COLUMNS = (
    ("size_native", "DOUBLE"),
    ("size_unit", "SYMBOL CAPACITY 16 CACHE"),
    ("contract_size", "DOUBLE"),
    ("contract_type", "SYMBOL CAPACITY 16 CACHE"),
    ("notional_usd", "DOUBLE"),
)


def _liquidity_ddl():
    """CREATE TABLE for the per-second order book liquidity metrics (see perps.py)."""
    columns = [
        "timestamp TIMESTAMP_NS",
        "exchange SYMBOL CAPACITY 256 CACHE",
        "symbol SYMBOL CAPACITY 256 CACHE",
        "contract_type SYMBOL CAPACITY 16 CACHE",
    ]
    columns += [f"{name} LONG" for name in perps.LIQUIDITY_LONG_COLUMNS]
    columns += [f"{name} DOUBLE" for name in perps.LIQUIDITY_DOUBLE_COLUMNS]
    body = ",\n        ".join(columns)
    return (
        f"CREATE TABLE IF NOT EXISTS perps_liquidity (\n        {body}\n"
        ") TIMESTAMP(timestamp) PARTITION BY DAY WAL"
    )


def _liquidity_1m_view_ddl():
    """1-minute averages of every liquidity metric; reach is the worst (minimum) coverage."""
    avg = [
        f"avg({c}) AS {c}" for c in perps.LIQUIDITY_DOUBLE_COLUMNS if not c.endswith("reach_bps")
    ]
    reach = [f"min({c}) AS {c}" for c in ("bid_reach_bps", "ask_reach_bps")]
    spread = ["min(spread_bps) AS spread_bps_min", "max(spread_bps) AS spread_bps_max"]
    select = ",\n        ".join(avg + reach + spread + ["count() AS samples"])
    return (
        "CREATE MATERIALIZED VIEW IF NOT EXISTS perps_liquidity_1m AS\n"
        f"    SELECT timestamp, exchange, symbol,\n        {select}\n"
        "    FROM perps_liquidity\n    SAMPLE BY 1m"
    )


PERPS_LIQUIDITY_DDL = _liquidity_ddl()
PERPS_LIQUIDITY_TTL_SQL = "ALTER TABLE perps_liquidity SET TTL 3 DAYS"
PERPS_LIQUIDITY_1M_DDL = _liquidity_1m_view_ddl()


def _perps_trades_view_ddl(name, interval):
    """OHLC, volume, USD notional, buy/sell notional and trade count of the perps trades."""
    return f"""
    CREATE MATERIALIZED VIEW IF NOT EXISTS {name} AS
    SELECT timestamp, exchange, symbol,
        first(price) AS open, max(price) AS high, min(price) AS low, last(price) AS close,
        sum(size) AS volume_base,
        sum(notional_usd) AS notional_usd,
        sum(CASE WHEN side = 'buy' THEN notional_usd ELSE 0.0 END) AS buy_notional_usd,
        sum(CASE WHEN side = 'sell' THEN notional_usd ELSE 0.0 END) AS sell_notional_usd,
        count() AS trades
    FROM trades
    WHERE contract_type <> 'spot'
    SAMPLE BY {interval}
"""


# 10 s buckets: the dashboard's rolling 1m/5m/15m volume windows and its price/volume series
# are sums over these, so the browser never touches raw trades
PERPS_TRADES_10S_DDL = _perps_trades_view_ddl("perps_trades_10s", "10s")
PERPS_TRADES_10S_TTL_SQL = "ALTER MATERIALIZED VIEW perps_trades_10s SET TTL 2 DAYS"

# Spot reference prices / volume in the same 10 s buckets (premium of perps vs spot)
SPOT_TRADES_10S_DDL = """
    CREATE MATERIALIZED VIEW IF NOT EXISTS spot_trades_10s AS
    SELECT timestamp, exchange, symbol,
        last(price) AS close,
        sum(size) AS volume_base,
        sum(notional_usd) AS notional_usd,
        count() AS trades
    FROM trades
    WHERE contract_type = 'spot'
    SAMPLE BY 10s
"""
SPOT_TRADES_10S_TTL_SQL = "ALTER MATERIALIZED VIEW spot_trades_10s SET TTL 2 DAYS"


def init_perps_liquidity(host, http_port):
    """Create the liquidity table and the views the perps dashboard reads (idempotent)."""
    print("\n  Creating perps liquidity table and views")
    steps = (
        ("perps_liquidity", PERPS_LIQUIDITY_DDL),
        ("TTL perps_liquidity (3 DAYS)", PERPS_LIQUIDITY_TTL_SQL),
        ("perps_liquidity_1m view", PERPS_LIQUIDITY_1M_DDL),
        ("perps_trades_10s view", PERPS_TRADES_10S_DDL),
        ("TTL perps_trades_10s (2 DAYS)", PERPS_TRADES_10S_TTL_SQL),
        ("spot_trades_10s view", SPOT_TRADES_10S_DDL),
        ("TTL spot_trades_10s (2 DAYS)", SPOT_TRADES_10S_TTL_SQL),
    )
    for label, sql in steps:
        result = execute_sql(host, http_port, sql)
        if result and (result.get("ddl") == "OK" or result.get("dataset") is not None):
            print(f"    ✅ {label}")
        else:
            error = "no response" if result is None else result.get("error", result)
            print(f"    ⚠️  {label}: {error}")


def execute_sql(host, port, sql):
    """Execute SQL via QuestDB HTTP REST API"""
    import json
    import urllib.parse
    import urllib.request

    # Use HTTP REST API
    url = f"http://{host}:{port}/exec"
    params = urllib.parse.urlencode({"query": sql})
    full_url = f"{url}?{params}"

    try:
        with urllib.request.urlopen(full_url) as response:
            result_text = response.read().decode()
            if result_text.strip() == "":
                # Empty response likely means successful DDL with no result
                return {"ddl": "OK"}
            result = json.loads(result_text)
            return result
    except Exception as e:
        print(f"Error executing SQL: {e}")
        return None


def init_tables(host="localhost", http_port=9000, wait_for_db=True, max_retries=30):
    """
    Initialize QuestDB tables with proper schema, TTL settings, and materialized views

    Args:
        host: QuestDB hostname (default: 'questdb' for Docker)
        http_port: HTTP port for REST API (default: 9000)
        wait_for_db: Wait for database to be ready (default: True)
        max_retries: Maximum connection retry attempts (default: 30)
    """

    if wait_for_db:
        print(f"Waiting for QuestDB at {host}:{http_port}...")
        # execute_sql returns None (it does not raise) while QuestDB is unreachable
        for attempt in range(max_retries):
            if execute_sql(host, http_port, "SELECT 1"):
                print("✅ QuestDB is ready!")
                break
            if attempt < max_retries - 1:
                print(f"  Attempt {attempt + 1}/{max_retries}: QuestDB not ready yet, waiting...")
                time.sleep(2)
            else:
                print(f"❌ Failed to connect to QuestDB after {max_retries} attempts")
                sys.exit(1)

    print("\n📊 Initializing QuestDB tables...")

    # Create trades table with proper crypto data types
    print("  Creating table: trades")
    trades_sql = """
        CREATE TABLE IF NOT EXISTS trades (
            timestamp TIMESTAMP_NS,
            exchange SYMBOL CAPACITY 256 CACHE,
            symbol SYMBOL CAPACITY 256 CACHE,
            price DOUBLE,
            size DOUBLE,
            side SYMBOL CAPACITY 256 CACHE,
            trade_id VARCHAR,
            size_native DOUBLE,
            size_unit SYMBOL CAPACITY 16 CACHE,
            contract_size DOUBLE,
            contract_type SYMBOL CAPACITY 16 CACHE,
            notional_usd DOUBLE
        ) TIMESTAMP(timestamp) PARTITION BY DAY WAL
    """
    result = execute_sql(host, http_port, trades_sql)
    if result and result.get("ddl") == "OK":
        print("    ✅ trades table created successfully")
    elif (
        result
        and "error" in result
        and "TABLE" in result["error"]
        and "exists" in result["error"].lower()
    ):
        print("    ⚠️  Table already exists (expected during recreation)")
    else:
        print(
            f"    ⚠️  Warning: {'Unknown error' if result is None else result.get('error', 'Unknown error')}"
        )

    # Verify trades table has correct timestamp type
    print("    Verifying trades table schema...")
    verify_sql = "SHOW COLUMNS FROM trades"
    result = execute_sql(host, http_port, verify_sql)
    if result and "dataset" in result:
        timestamp_col = next((col for col in result["dataset"] if col[0] == "timestamp"), None)
        if timestamp_col:
            actual_type = timestamp_col[1]
            if actual_type == "TIMESTAMP_NS":
                print(
                    f"    ✅ trades.timestamp has correct type: {actual_type} (nanosecond precision)"
                )
            else:
                print(
                    f"    ⚠️  WARNING: trades.timestamp has type {actual_type}, expected TIMESTAMP_NS"
                )
                print("       This may cause precision issues. Consider dropping and recreating.")

    # Contract / notional columns (idempotent; upgrades tables created before they existed)
    for column, column_type in TRADES_EXTRA_COLUMNS:
        sql = f"ALTER TABLE trades ADD COLUMN IF NOT EXISTS {column} {column_type}"
        result = execute_sql(host, http_port, sql)
        if not (result and result.get("ddl") == "OK"):
            print(f"    ⚠️  {sql}: {result}")

    # Enable deduplication (idempotent: also applies to tables created before this was added;
    # rows already stored are not rewritten)
    print("    Configuring DEDUP: trades (" + ", ".join(TRADES_DEDUP_KEYS) + ")")
    result = execute_sql(host, http_port, TRADES_DEDUP_SQL)
    if result and result.get("ddl") == "OK":
        print("    ✅ DEDUP UPSERT KEYS set for trades")
    else:
        print(
            f"    ⚠️  DEDUP warning: {'Unknown error' if result is None else result.get('error', result)}"
        )

    # Create orderbooks table with QuestDB double arrays for efficient storage
    # SDK v3.0.0+ supports 2D numpy arrays natively for DOUBLE[][] columns
    print("  Creating table: orderbooks")
    orderbooks_sql = """
        CREATE TABLE IF NOT EXISTS orderbooks (
            timestamp TIMESTAMP_NS,
            exchange SYMBOL CAPACITY 256 CACHE,
            symbol SYMBOL CAPACITY 256 CACHE,
            bids DOUBLE[][],  -- 2D array: [[prices...], [volumes...]]
            asks DOUBLE[][]   -- 2D array: [[prices...], [volumes...]]
        ) TIMESTAMP(timestamp) PARTITION BY HOUR WAL
    """
    result = execute_sql(host, http_port, orderbooks_sql)
    if result and (result.get("ddl") == "OK" or result.get("dataset") is not None):
        print("    ✅ orderbooks table created successfully")
    elif (
        result
        and "error" in result
        and "TABLE" in result["error"]
        and "exists" in result["error"].lower()
    ):
        print("    ✅ orderbooks table already exists")
    else:
        print(
            f"    ⚠️  Warning: {'Unknown error' if result is None else result.get('error', 'Unknown error')}"
        )

    # Verify orderbooks table has correct timestamp type
    print("    Verifying orderbooks table schema...")
    verify_sql = "SHOW COLUMNS FROM orderbooks"
    result = execute_sql(host, http_port, verify_sql)
    if result and "dataset" in result:
        timestamp_col = next((col for col in result["dataset"] if col[0] == "timestamp"), None)
        if timestamp_col:
            actual_type = timestamp_col[1]
            if actual_type == "TIMESTAMP_NS":
                print(
                    f"    ✅ orderbooks.timestamp has correct type: {actual_type} (nanosecond precision)"
                )
            else:
                print(
                    f"    ⚠️  WARNING: orderbooks.timestamp has type {actual_type}, expected TIMESTAMP_NS"
                )
                print("       This may cause precision issues. Consider dropping and recreating.")

    # Apply TTL to orderbooks (1 hour retention)
    print("    Configuring TTL: orderbooks (1 HOURS)")
    ttl_sql = "ALTER TABLE orderbooks SET TTL 1 HOURS"
    result = execute_sql(host, http_port, ttl_sql)
    if result and result.get("ddl") == "OK":
        print("    ✅ TTL set to 1 HOURS for orderbooks")
    elif result and "error" in result and "already" in result["error"].lower():
        print("    ✅ TTL already configured for orderbooks")
    elif result and "error" in result:
        print(f"    ⚠️  TTL warning: {result['error']}")
    else:
        print("    ✅ TTL applied to orderbooks")

    # Create materialized view with IF NOT EXISTS
    print("  Creating materialized view: orderbooks_1s")
    mv_sql = """
        CREATE MATERIALIZED VIEW IF NOT EXISTS orderbooks_1s AS 
        SELECT 
            timestamp, 
            exchange, 
            symbol, 
            last(bids) AS bids, 
            last(asks) AS asks 
        FROM orderbooks 
        SAMPLE BY 1s
    """
    result = execute_sql(host, http_port, mv_sql)
    if result and result.get("ddl") == "OK":
        print("    ✅ Materialized view created successfully")
    elif (
        result
        and "error" in result
        and "VIEW" in result["error"]
        and "exists" in result["error"].lower()
    ):
        print("    ✅ Materialized view already exists")
    elif result and "error" in result:
        print(f"    ❌ Error creating view: {result['error']}")
    else:
        print("    ✅ Materialized view created")

    # Verify schemas
    print("\n  Verifying table schemas...")

    # Verify trades table schema
    result = execute_sql(host, http_port, "SELECT \"column\", type FROM table_columns('trades')")
    if result and result.get("dataset"):
        print("    ✅ trades table schema verified")
        print(f"      Columns: {[col[0] for col in result['dataset']]}")

    # Verify orderbooks table schema
    result = execute_sql(
        host, http_port, "SELECT \"column\", type FROM table_columns('orderbooks')"
    )
    if result and result.get("dataset"):
        print("    ✅ orderbooks table schema verified")
        array_columns = [col[0] for col in result["dataset"] if "[]" in col[1]]
        if array_columns:
            print(f"      Array columns: {array_columns}")

    # Verify materialized view
    mv_check_sql = "SELECT view_name FROM materialized_views() WHERE view_name = 'orderbooks_1s'"
    result = execute_sql(host, http_port, mv_check_sql)
    if result and "dataset" in result and result["dataset"]:
        print("    ✅ materialized view orderbooks_1s verified")

    init_perps_liquidity(host, http_port)

    print("\n✅ QuestDB initialization complete!\n")

    # Print configuration summary
    print("📋 Table Configuration Summary:")
    print(f"  {'Table':<25} {'Type':<25} {'Partition':<10} {'TTL':<15}")
    print(f"  {'-' * 25} {'-' * 25} {'-' * 10} {'-' * 15}")
    print(f"  {'trades':<25} {'DEDUP on trade_id':<25} {'DAY':<10} {'None':<15}")
    print(f"  {'orderbooks':<25} {'DOUBLE[][] Arrays':<25} {'HOUR':<10} {'1 HOURS':<15}")
    print(f"  {'orderbooks_1s':<25} {'Materialized View':<25} {'N/A':<10} {'N/A':<15}")
    print()
    print("📊 Ready for crypto data ingestion!")
    print("   - Trades table will store individual trade events")
    print("   - Orderbooks table uses efficient double arrays for bid/ask data")
    print("   - Materialized view provides 1-second sampled orderbook snapshots")
    print()


if __name__ == "__main__":
    # Parse command line arguments
    import sys

    host = sys.argv[1] if len(sys.argv) > 1 else "questdb"

    init_tables(host=host, wait_for_db=True)
