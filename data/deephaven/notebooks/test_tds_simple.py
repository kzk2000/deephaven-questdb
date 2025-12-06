"""
Simple TableDataService test to understand the API
Based on Deephaven documentation example
"""

from deephaven.experimental.table_data_service import (
    TableDataService,
    TableDataServiceBackend,
    TableKey,
    TableLocationKey,
)
from typing import Callable, Optional
import pyarrow as pa

print("="*70)
print("Testing TableDataService API")
print("="*70)

# Simple TableKey implementation
class SimpleTableKey(TableKey):
    def __init__(self, key: str):
        self.key = key

    def __hash__(self):
        return hash(self.key)

    def __eq__(self, other):
        if not isinstance(other, SimpleTableKey):
            return NotImplemented
        return self.key == other.key

    def __str__(self):
        return f"SimpleTableKey({self.key})"

# Simple TableLocationKey implementation  
class SimpleTableLocationKey(TableLocationKey):
    def __init__(self, key: str):
        self.key = key

    def __hash__(self):
        return hash(self.key)

    def __eq__(self, other):
        if not isinstance(other, SimpleTableLocationKey):
            return NotImplemented
        return self.key == other.key

    def __str__(self):
        return f"SimpleTableLocationKey({self.key})"

# Simple backend with hardcoded test data
class SimpleBackend(TableDataServiceBackend):
    def __init__(self):
        # Hardcoded test schema and data
        self.schema = pa.schema([
            pa.field("id", pa.int64()),
            pa.field("value", pa.float64()),
            pa.field("name", pa.string())
        ])
        self.data = pa.table({
            "id": [1, 2, 3],
            "value": [10.5, 20.7, 30.9],
            "name": ["A", "B", "C"]
        })

    def table_schema(self, table_key, schema_cb, failure_cb):
        try:
            print(f"  table_schema called for {table_key}")
            schema_cb(self.schema, None)  # data schema, no partitioning schema
        except Exception as e:
            print(f"  ERROR in table_schema: {e}")
            failure_cb(e)

    def table_locations(self, table_key, location_cb, success_cb, failure_cb):
        try:
            print(f"  table_locations called for {table_key}")
            # Single location, no partitioning
            loc_key = SimpleTableLocationKey("main")
            location_cb(loc_key, None)
            success_cb()
        except Exception as e:
            print(f"  ERROR in table_locations: {e}")
            import traceback
            traceback.print_exc()
            failure_cb(e)

    def table_location_size(self, table_key, table_location_key, size_cb, failure_cb):
        try:
            print(f"  table_location_size called for {table_key}, {table_location_key}")
            size_cb(self.data.num_rows)
        except Exception as e:
            print(f"  ERROR in table_location_size: {e}")
            failure_cb(e)

    def column_values(self, table_key, table_location_key, col, offset, min_rows, max_rows, values_cb, failure_cb):
        try:
            print(f"  column_values called for {table_key}, col={col}, offset={offset}, max_rows={max_rows}")
            # Return column slice
            col_data = self.data.select([col]).slice(offset, max_rows)
            values_cb(col_data)
        except Exception as e:
            print(f"  ERROR in column_values: {e}")
            failure_cb(e)

    def subscribe_to_table_locations(self, table_key, location_cb, success_cb, failure_cb):
        try:
            print(f"  subscribe_to_table_locations called for {table_key}")
            # Send existing location
            loc_key = SimpleTableLocationKey("main")
            location_cb(loc_key, None)
            success_cb()
            
            # Return unsubscribe function
            def unsubscribe():
                print(f"  unsubscribed from table_locations for {table_key}")
                
            return unsubscribe
        except Exception as e:
            print(f"  ERROR in subscribe_to_table_locations: {e}")
            import traceback
            traceback.print_exc()
            failure_cb(e)
            return lambda: None

    def subscribe_to_table_location_size(self, table_key, table_location_key, size_cb, success_cb, failure_cb):
        try:
            print(f"  subscribe_to_table_location_size called for {table_key}, {table_location_key}")
            # Send current size
            size_cb(self.data.num_rows)
            success_cb()
            
            # Return unsubscribe function
            def unsubscribe():
                print(f"  unsubscribed from table_location_size for {table_key}")
                
            return unsubscribe
        except Exception as e:
            print(f"  ERROR in subscribe_to_table_location_size: {e}")
            failure_cb(e)
            return lambda: None

# Test it
print("\n1. Creating backend and service...")
backend = SimpleBackend()
service = TableDataService(backend, page_size=100)

print("\n2. Creating table key...")
test_key = SimpleTableKey("test_table")

print("\n3. Creating static table (refreshing=False)...")
try:
    static_table = service.make_table(test_key, refreshing=False)
    print(f"  ✅ Static table created: {static_table.size} rows")
except Exception as e:
    print(f"  ❌ ERROR: {e}")
    import traceback
    traceback.print_exc()

print("\n4. Creating refreshing table (refreshing=True)...")
try:
    from deephaven.liveness_scope import LivenessScope
    scope = LivenessScope()
    
    with scope.open():
        refresh_table = service.make_table(test_key, refreshing=True)
        print(f"  ✅ Refreshing table created: {refresh_table.size} rows")
except Exception as e:
    print(f"  ❌ ERROR: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "="*70)
print("Test complete!")
print("="*70)
