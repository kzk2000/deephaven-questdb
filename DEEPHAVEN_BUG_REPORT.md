# Bug Report: TableDataService API Broken in Edge Build

## Summary

The experimental `TableDataService` API is completely non-functional in the current edge build due to a callback proxy implementation error. When attempting to create either static or refreshing tables using a custom `TableDataServiceBackend`, the system fails with `AttributeError: object has no attribute 'apply'`.

## Environment

- **Deephaven Version**: `0.41.0+snapshot` (edge build)
- **Docker Image**: `ghcr.io/deephaven/server:edge`
- **Python Version**: 3.12.3
- **Date Tested**: December 6, 2024

## Expected Behavior

According to the [official TableDataService documentation](https://deephaven.io/core/docs/how-to-guides/data-import-export/table-data-service/), custom backend implementations should call the provided callbacks directly as Python callables:

```python
def subscribe_to_table_locations(self, table_key, location_cb, success_cb, failure_cb):
    """Subscribe to table locations with a callable."""
    # Documentation shows callbacks are called directly
    for key, location in table.locations.items():
        location_cb(key, location.partitioning_values)  # Direct call
    success_cb()  # Direct call
    return unsubscribe_function
```

## Actual Behavior

The library's internal `location_cb_proxy` function attempts to call Java methods (`.apply()` or `.accept()`) on callback objects that don't have these methods, resulting in:

```
AttributeError: 'io.deephaven.extensions.barrage.util.PythonTableDataService$BackendAccessor$$Lambda/0x00007f2b989d69' object has no attribute 'apply'
```

## Full Error Stack Trace

```
Caused by: io.deephaven.engine.table.impl.locations.TableDataException: 
PythonTableDataService.TableKeyImpl[key=QuestDBTableKey('trades')]: subscribe_to_table_locations failed
    at io.deephaven.extensions.barrage.util.PythonTableDataService$TableLocationProviderImpl.lambda$activateUnderlyingDataSource$3(PythonTableDataService.java:657)
    at io.deephaven.extensions.barrage.util.PythonTableDataService$BackendAccessor.lambda$subscribeToTableLocations$8(PythonTableDataService.java:275)
    at org.jpy.PyLib.callAndReturnObject(Native Method)
    at org.jpy.PyObject.call(PyObject.java:444)
    at io.deephaven.extensions.barrage.util.PythonTableDataService$BackendAccessor.subscribeToTableLocations(PythonTableDataService.java:278)
    at io.deephaven.extensions.barrage.util.PythonTableDataService$TableLocationProviderImpl.activateUnderlyingDataSource(PythonTableDataService.java:653)
    ... 22 more
Caused by: io.deephaven.UncheckedDeephavenException: 
'io.deephaven.extensions.barrage.util.PythonTableDataService$BackendAccessor$$Lambda/0x00007f2b989d69' object has no attribute 'apply'
Traceback (most recent call last):
  File "<string>", line 163, in subscribe_to_table_locations
  File "/opt/deephaven/venv/lib/python3.12/site-packages/deephaven/experimental/table_data_service.py", line 459, in location_cb_proxy
    location_cb.apply(
    ^^^^^^^^^^^^^^^^^
AttributeError: 'io.deephaven.extensions.barrage.util.PythonTableDataService$BackendAccessor$$Lambda/0x00007f2b989d69' object has no attribute 'apply'
```

## Root Cause

In `/opt/deephaven/venv/lib/python3.12/site-packages/deephaven/experimental/table_data_service.py`, the `location_cb_proxy` function attempts to call methods on Java lambda objects that don't exist:

**Lines affected:**
```bash
$ grep -n "location_cb.accept\|location_cb.apply" table_data_service.py
404:                location_cb.apply(          # table_locations method
416:                location_cb.accept(         # table_locations method
459:                location_cb.apply(          # subscribe_to_table_locations method
471:                location_cb.accept(         # subscribe_to_table_locations method
```

**Problematic code pattern (around line 455-475):**
```python
def location_cb_proxy(
    pt_location_key: TableLocationKey, pt_table: Optional[pa.Table] = None
):
    j_tbl_location_key = _JTableLocationKeyImpl(pt_location_key)
    if pt_table is None:
        location_cb.apply(                           # ❌ Method doesn't exist
            j_tbl_location_key, jpy.array("java.nio.ByteBuffer", [])
        )
    else:
        if pt_table.num_rows != 1:
            raise ValueError(
                "The number of rows in the pyarrow table for partitioning column values must be 1"
            )
        bb_list = [
            jpy.byte_buffer(rb.serialize()) for rb in pt_table.to_batches()
        ]
        bb_list.insert(0, jpy.byte_buffer(pt_table.schema.serialize()))
        location_cb.accept(                          # ❌ Method doesn't exist
            j_tbl_location_key, jpy.array("java.nio.ByteBuffer", bb_list)
        )
```

## Minimal Reproducible Example

```python
from deephaven.experimental.table_data_service import (
    TableDataService,
    TableDataServiceBackend,
    TableKey,
    TableLocationKey,
)
import pyarrow as pa

class SimpleTableKey(TableKey):
    def __init__(self, name: str):
        self.name = name
    
    def __hash__(self):
        return hash(self.name)
    
    def __eq__(self, other):
        return isinstance(other, SimpleTableKey) and self.name == other.name

class SimpleLocationKey(TableLocationKey):
    def __init__(self, name: str):
        self.name = name
    
    def __hash__(self):
        return hash(self.name)
    
    def __eq__(self, other):
        return isinstance(other, SimpleLocationKey) and self.name == other.name

class SimpleBackend(TableDataServiceBackend):
    def table_schema(self, table_key, schema_cb, failure_cb):
        try:
            schema = pa.schema([pa.field("id", pa.int64())])
            schema_cb(schema, None)
        except Exception as e:
            failure_cb(e)
    
    def table_locations(self, table_key, location_cb, success_cb, failure_cb):
        try:
            # Following the documentation example - direct callback call
            location_cb(SimpleLocationKey("loc1"), None)
            success_cb()
        except Exception as e:
            failure_cb(e)
    
    def subscribe_to_table_locations(self, table_key, location_cb, success_cb, failure_cb):
        try:
            # Following the documentation example - direct callback call
            location_cb(SimpleLocationKey("loc1"), None)  # ❌ Fails here
            success_cb()
            return lambda: None
        except Exception as e:
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

# Create service and attempt to make a table
backend = SimpleBackend()
service = TableDataService(backend)
table_key = SimpleTableKey("test")

# This will fail with: AttributeError: object has no attribute 'apply'
result = service.make_table(table_key, refreshing=True)
```

## Impact

**Critical:** The entire TableDataService API is unusable in the current edge build. Both static and refreshing table creation fail at the location subscription stage.

Affected methods:
- ❌ `table_locations()` - Cannot create static tables
- ❌ `subscribe_to_table_locations()` - Cannot create refreshing tables
- Result: **API completely non-functional**

## Analysis

The issue appears to be a mismatch between:

1. **Python-side API contract**: Backend implementations call callbacks directly as Python callables (per documentation)
2. **Java-to-Python bridge layer**: The `location_cb_proxy` tries to invoke Java methods (`.apply()`, `.accept()`) on callback objects
3. **Java-side callback objects**: Lambda instances that don't have `.apply()` or `.accept()` methods

The proxy layer seems to expect different Java callback types than what's actually being passed from the Java side.

## Possible Solutions

### Option 1: Fix the Proxy Layer
The `location_cb_proxy` should detect the callback type and call it appropriately, or the callbacks should be wrapped differently from the Java side.

### Option 2: Use Direct Invocation
Instead of calling `.apply()` or `.accept()`, the proxy could use `__call__()` or direct invocation, matching how Python backends naturally call these callbacks.

### Option 3: Update Documentation
If the current behavior is intentional, the documentation should be updated to show how backends need to handle these callback objects differently.

## Additional Context

- TableDataService API was added to main branch on November 1, 2024
- Not present in any v0.40.x releases
- Testing with QuestDB as backend to create WAL-driven streaming tables
- The backend implementation follows the official documentation examples exactly
- Same error occurs with both `table_locations` and `subscribe_to_table_locations` methods

## Related Files

- `/opt/deephaven/venv/lib/python3.12/site-packages/deephaven/experimental/table_data_service.py`
  - Lines 404, 416 (`table_locations` method)
  - Lines 459, 471 (`subscribe_to_table_locations` method)
- Official documentation: https://deephaven.io/core/docs/how-to-guides/data-import-export/table-data-service/

## Workaround

None available - the bug is in the library's callback proxy layer, not addressable from user code.

Currently using direct PostgreSQL queries to QuestDB with periodic refresh as an alternative until this is resolved.

## Request

Please investigate and fix the callback proxy implementation to either:
1. Match the documented API behavior (direct Python callable invocation), or
2. Update the documentation to reflect the actual expected implementation

This API would be extremely valuable for integrating external data sources (like QuestDB) with real-time WAL-driven updates, but it's currently unusable due to this bug.
