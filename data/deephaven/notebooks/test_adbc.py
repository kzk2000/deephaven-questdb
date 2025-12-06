
"""
Test ADBC (Arrow Database Connectivity) with QuestDB

This tests using the ADBC PostgreSQL driver to query QuestDB
with zero-copy Arrow data transfer.
"""

from adbc_driver_postgresql import dbapi
import deephaven.arrow as dharrow
import pyarrow

# QuestDB connection via ADBC
# Note: Use 'questdb' hostname in Docker, 'localhost' from host
uri = "postgresql://questdb:8812/qdb?user=admin&password=quest"
print(f"Connecting to: {uri}")

# Test query - get recent BTC trades
query = """
    SELECT * FROM trades
    WHERE symbol = 'BTC-USD'
    ORDER BY timestamp DESC
    LIMIT 200
"""

print("\nExecuting query via ADBC...")
try:
    with dbapi.connect(uri) as conn:
        with conn.cursor() as cursor:
            cursor.execute(query)
            
            # Fetch as Arrow table
            arrow_table = cursor.fetch_arrow_table()
            print(f"✅ Fetched Arrow table: {arrow_table.num_rows} rows, {arrow_table.num_columns} columns")
            print(f"   Schema: {arrow_table.schema}")
            
            # Convert to Deephaven table
            trades_adbc = dharrow.to_table(arrow_table)
            print(f"\n✅ Converted to Deephaven table: {trades_adbc.size} rows")
            
            # Show summary
            print("\nTrades summary:")
            print(f"  Columns: {trades_adbc.columns}")
            
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "="*60)
print("ADBC Test Complete")
print("="*60)