# Test TableDataService API in Deephaven v0.40.7
# This will tell us if the API exists and if the bug is present

print("="*70)
print("Testing TableDataService in v0.40.7")
print("="*70)

# Step 1: Check if API exists
print("\n1. Checking if TableDataService API is available...")
try:
    from deephaven.experimental.table_data_service import (
        TableDataService,
        TableDataServiceBackend,
        TableKey,
        TableLocationKey,
    )
    import pyarrow as pa
    print("   [OK] TableDataService API is AVAILABLE in v0.40.7!")
    print("   This is unexpected - we thought it was added after v0.40.7")
except ImportError as e:
    print(f"   [FAIL] TableDataService NOT available: {e}")
    print("   Expected result - API was added Nov 1, 2024")
    exit()

# Step 2: Test basic TableKey implementation
print("\n2. Testing TableKey implementation...")
try:
    class TestTableKey(TableKey):
        def __init__(self, name):
            self.name = name
        def __hash__(self):
            return hash(self.name)
        def __eq__(self, other):
            return isinstance(other, TestTableKey) and self.name == other.name
    
    key = TestTableKey("test")
    print(f"   [OK] TableKey works: {key}")
except Exception as e:
    print(f"   [FAIL] Error: {e}")

# Step 3: Test TableLocationKey implementation
print("\n3. Testing TableLocationKey implementation...")
try:
    class TestLocationKey(TableLocationKey):
        def __init__(self, name):
            self.name = name
        def __hash__(self):
            return hash(self.name)
        def __eq__(self, other):
            return isinstance(other, TestLocationKey) and self.name == other.name
    
    loc_key = TestLocationKey("loc1")
    print(f"   [OK] TableLocationKey works: {loc_key}")
except Exception as e:
    print(f"   [FAIL] Error: {e}")

# Step 4: Test minimal backend implementation
print("\n4. Testing minimal TableDataServiceBackend...")
try:
    class MinimalBackend(TableDataServiceBackend):
        def table_schema(self, table_key, schema_cb, failure_cb):
            try:
                schema = pa.schema([pa.field("id", pa.int64())])
                schema_cb(schema, None)
                print("   [OK] table_schema callback works")
            except Exception as e:
                print(f"   [FAIL] table_schema error: {e}")
                failure_cb(e)
        
        def table_locations(self, table_key, location_cb, success_cb, failure_cb):
            try:
                print("   -> Calling location_cb...")
                location_cb(TestLocationKey("loc1"), None)
                print("   [OK] location_cb succeeded")
                success_cb()
                print("   [OK] table_locations completed")
            except Exception as e:
                print(f"   [FAIL] table_locations error: {e}")
                import traceback
                traceback.print_exc()
                failure_cb(e)
        
        def subscribe_to_table_locations(self, table_key, location_cb, success_cb, failure_cb):
            try:
                print("   -> Calling location_cb in subscribe...")
                location_cb(TestLocationKey("loc1"), None)
                print("   [OK] location_cb succeeded")
                success_cb()
                print("   [OK] subscribe_to_table_locations completed")
                return lambda: None
            except Exception as e:
                print(f"   [FAIL] subscribe_to_table_locations error: {e}")
                import traceback
                traceback.print_exc()
                failure_cb(e)
                return lambda: None
        
        def table_location_size(self, table_key, table_location_key, size_cb, failure_cb):
            try:
                size_cb(0)
            except Exception as e:
                failure_cb(e)
        
        def subscribe_to_table_location_size(self, table_key, table_location_key, 
                                             size_cb, success_cb, failure_cb):
            try:
                size_cb(0)
                success_cb()
                return lambda: None
            except Exception as e:
                failure_cb(e)
                return lambda: None
        
        def column_values(self, table_key, table_location_key, col, offset, 
                          min_rows, max_rows, values_cb, failure_cb):
            try:
                empty_table = pa.table({col: pa.array([], type=pa.int64())})
                values_cb(empty_table)
            except Exception as e:
                failure_cb(e)
    
    backend = MinimalBackend()
    print("   [OK] MinimalBackend created")
except Exception as e:
    print(f"   [FAIL] Backend creation error: {e}")
    import traceback
    traceback.print_exc()

# Step 5: Test TableDataService creation
print("\n5. Testing TableDataService creation...")
try:
    service = TableDataService(backend)
    print("   [OK] TableDataService created")
except Exception as e:
    print(f"   [FAIL] Service creation error: {e}")
    import traceback
    traceback.print_exc()

# Step 6: Test make_table (this is where the bug might occur)
print("\n6. Testing make_table with refreshing=True...")
print("   This is where the callback bug occurs in edge build...")
try:
    table_key = TestTableKey("test")
    result = service.make_table(table_key, refreshing=True)
    print(f"   [OK] make_table succeeded! Table: {result}")
    print("\n   SUCCESS NO BUG IN v0.40.7! The API works!")
except AttributeError as e:
    if "'apply'" in str(e):
        print(f"   [FAIL] CALLBACK BUG FOUND: {e}")
        print("\n   This is the same bug as in edge build")
        print("   The bug exists in both v0.40.7 AND edge build")
    else:
        print(f"   [FAIL] Different AttributeError: {e}")
except Exception as e:
    print(f"   [FAIL] Unexpected error: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "="*70)
print("TEST COMPLETE")
print("="*70)
