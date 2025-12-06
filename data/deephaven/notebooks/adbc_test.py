"""
Test ADBC (Arrow Database Connectivity) with QuestDB

This demonstrates using the ADBC PostgreSQL driver to query QuestDB
with zero-copy Arrow data transfer to Deephaven.

Run this from Deephaven UI (http://localhost:10000):
  exec(open('/data/notebooks/adbc_test.py').read())

Reference: https://deephaven.io/core/docs/how-to-guides/data-import-export/execute-sql-queries/
"""

from adbc_driver_postgresql import dbapi
from deephaven.dbc import adbc as dhadbc
from deephaven import agg

print("="*70)
print("Testing ADBC (Arrow Database Connectivity) with QuestDB")
print("="*70)

# QuestDB connection via ADBC PostgreSQL driver
uri = "postgresql://questdb:8812/qdb?user=admin&password=quest"
print(f"\n🔌 Connecting to: {uri}")

# Test query - get recent BTC trades
query = """
    SELECT * FROM trades
    WHERE symbol = 'BTC-USD'
    ORDER BY timestamp DESC
    LIMIT 200
"""

print("\n📊 Executing query via ADBC...")

try:
    with dbapi.connect(uri) as conn:
        with conn.cursor() as cursor:
            cursor.execute(query)
            
            # Use Deephaven's ADBC helper to convert cursor to table
            # This uses Arrow for zero-copy data transfer
            trades_adbc = dhadbc.read_cursor(cursor)
            
            print(f"\n✅ Created Deephaven table via ADBC:")
            print(f"   Size: {trades_adbc.size} rows")
            print(f"   Columns: {', '.join(trades_adbc.columns)}")
            
            # Create filtered views
            trades_adbc_buy = trades_adbc.where("side == 'buy'")
            trades_adbc_sell = trades_adbc.where("side == 'sell'")
            
            # Summary statistics
            trades_adbc_summary = trades_adbc.agg_by([
                agg.count_("trade_count"),
                agg.avg("avg_price = price"),
                agg.sum_("total_volume = amount"),
                agg.min_("min_price = price"),
                agg.max_("max_price = price")
            ], by=["exchange"])
            
            print(f"\n📋 Tables created:")
            print(f"   - trades_adbc: All BTC trades ({trades_adbc.size} rows)")
            print(f"   - trades_adbc_buy: Buy orders ({trades_adbc_buy.size} rows)")
            print(f"   - trades_adbc_sell: Sell orders ({trades_adbc_sell.size} rows)")
            print(f"   - trades_adbc_summary: Per-exchange summary")
            
            print("\n" + "="*70)
            print("✅ ADBC Test SUCCESSFUL!")
            print("="*70)
            print("\nUsage:")
            print("  trades_adbc              # View all BTC trades")
            print("  trades_adbc.tail(20)     # View latest 20 trades")
            print("  trades_adbc_buy          # View buy orders only")
            print("  trades_adbc_sell         # View sell orders only")
            print("  trades_adbc_summary      # View summary by exchange")
            
except Exception as e:
    print(f"\n❌ Error: {e}")
    import traceback
    traceback.print_exc()
    print("\n" + "="*70)
    print("❌ ADBC Test FAILED")
    print("="*70)
    print("\nTroubleshooting:")
    print("  - Ensure QuestDB is running and accessible")
    print("  - Check that trades table exists and has BTC-USD data")
    print("  - Verify ADBC driver is installed: pip list | grep adbc")
