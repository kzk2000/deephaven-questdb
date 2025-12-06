# Test QuestDB connection from Deephaven
import sys
sys.path.insert(0, '/data/storage/notebooks')

print("Testing QuestDB connection...")

try:
    import qdb
    print("✓ qdb module imported")
    
    print("\n1. Testing get_connection()...")
    conn = qdb.get_connection()
    print("✓ Connection established")
    
    cursor = conn.cursor()
    print("✓ Cursor created")
    
    print("\n2. Testing trades table exists...")
    cursor.execute("SELECT count(*) FROM trades")
    count = cursor.fetchone()[0]
    print(f"✓ trades table has {count:,} rows")
    
    print("\n3. Testing wal_tables() function...")
    cursor.execute("SELECT * FROM wal_tables() WHERE name = 'trades'")
    wal_info = cursor.fetchall()
    print(f"✓ wal_tables() returned {len(wal_info)} row(s)")
    if wal_info:
        print(f"  WAL info: {wal_info[0]}")
    
    print("\n4. Testing information_schema...")
    cursor.execute("""
        SELECT column_name, data_type 
        FROM information_schema.columns 
        WHERE table_name = 'trades'
        ORDER BY ordinal_position
    """)
    cols = cursor.fetchall()
    print(f"✓ Schema has {len(cols)} columns:")
    for col_name, col_type in cols:
        print(f"    {col_name}: {col_type}")
    
    cursor.close()
    conn.close()
    
    print("\n✓✓✓ ALL TESTS PASSED ✓✓✓")
    
except Exception as e:
    print(f"\n✗ ERROR: {e}")
    import traceback
    traceback.print_exc()
