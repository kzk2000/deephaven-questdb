# Minimal TableDataService test with better error handling
print("="*70)
print("Testing TableDataService with QuestDB backend")
print("="*70)

# Step 1: Test basic imports
print("\n1. Testing imports...")
try:
    from deephaven.experimental.table_data_service import (
        TableDataService,
        TableDataServiceBackend,
        TableKey,
        TableLocationKey,
    )
    import pyarrow as pa
    import qdb
    print("   ✓ All imports successful")
except Exception as e:
    print(f"   ✗ Import failed: {e}")
    raise

# Step 2: Test QuestDB connection
print("\n2. Testing QuestDB connection...")
try:
    conn = qdb.get_connection()
    print("   ✓ Connection established")
    
    cursor = conn.cursor()
    cursor.execute("SELECT count(*) FROM trades")
    count = cursor.fetchone()[0]
    print(f"   ✓ trades table has {count:,} rows")
    
    cursor.close()
    conn.close()
except Exception as e:
    print(f"   ✗ Connection failed: {e}")
    print("\n   Possible issues:")
    print("   - QuestDB container not running")
    print("   - Network connectivity issue")
    print("   - Wrong hostname (should be 'questdb' in Docker)")
    raise

# Step 3: Test schema introspection
print("\n3. Testing schema introspection...")
try:
    query = """
        SELECT column_name, data_type 
        FROM information_schema.columns 
        WHERE table_name = 'trades'
        ORDER BY ordinal_position
    """
    with qdb.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            cols = cur.fetchall()
    
    print(f"   ✓ Found {len(cols)} columns")
    for col_name, col_type in cols[:3]:
        print(f"     - {col_name}: {col_type}")
    
except Exception as e:
    print(f"   ✗ Schema query failed: {e}")
    raise

# Step 4: Test WAL tables function
print("\n4. Testing wal_tables() function...")
try:
    with qdb.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM wal_tables() WHERE name = 'trades'")
            wal_info = cur.fetchall()
    
    if wal_info:
        print(f"   ✓ WAL enabled for trades table")
        print(f"     {wal_info[0]}")
    else:
        print("   ⚠ No WAL info found (table might not be WAL-enabled)")
    
except Exception as e:
    print(f"   ✗ WAL query failed: {e}")
    raise

print("\n" + "="*70)
print("✓✓✓ ALL CONNECTIVITY TESTS PASSED ✓✓✓")
print("="*70)
print("\nNext step: Test TableDataService backend...")
