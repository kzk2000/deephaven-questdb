# Bug Report: TableDataService API - Callback Method Not Found

## Summary

The experimental `TableDataService` API is non-functional due to a callback proxy error. When creating tables using a custom `TableDataServiceBackend`, the system fails with `AttributeError: object has no attribute 'apply'`.

## Affected Versions

- ✅ **v0.40.7 (stable)** - Confirmed broken
- ✅ **v0.41.0+snapshot (edge)** - Confirmed broken
- ❓ Likely affects all versions with TableDataService API

## Environment

**v0.40.7 Test:**
- Docker Image: `ghcr.io/deephaven/server:0.40.7`
- Python: 3.10.12
- Error Location: `table_data_service.py` line 378

**Edge Build Test:**
- Docker Image: `ghcr.io/deephaven/server:edge`
- Python: 3.12.3
- Error Location: `table_data_service.py` line 459

**Date Tested**: December 6, 2024

## The Bug

The `location_cb_proxy` function calls methods that don't exist on Java lambda callback objects:

```python
# In table_data_service.py (lines vary by version)
def location_cb_proxy(pt_location_key, pt_table):
    j_tbl_location_key = _JTableLocationKeyImpl(pt_location_key)
    if pt_table is None:
        location_cb.apply(j_tbl_location_key, jpy.array("java.nio.ByteBuffer", []))
        # ❌ AttributeError: object has no attribute 'apply'
    else:
        # ... prepare data ...
        location_cb.accept(j_tbl_location_key, jpy.array("java.nio.ByteBuffer", bb_list))
        # ❌ AttributeError: object has no attribute 'accept'
```

## Error Message

```
AttributeError: 'io.deephaven.extensions.barrage.util.PythonTableDataService$BackendAccessor$$Lambda/...' 
object has no attribute 'apply'

Traceback:
  File "table_data_service.py", line 378 (v0.40.7) / 459 (edge)
  location_cb.apply(j_tbl_location_key, jpy.array("java.nio.ByteBuffer", []))
```

## Minimal Reproducible Example

```python
from deephaven.experimental.table_data_service import (
    TableDataService, TableDataServiceBackend, TableKey, TableLocationKey
)
import pyarrow as pa

class TestKey(TableKey):
    def __init__(self, name): self.name = name
    def __hash__(self): return hash(self.name)
    def __eq__(self, other): return isinstance(other, TestKey) and self.name == other.name

class TestLocationKey(TableLocationKey):
    def __init__(self, name): self.name = name
    def __hash__(self): return hash(self.name)
    def __eq__(self, other): return isinstance(other, TestLocationKey) and self.name == other.name

class MinimalBackend(TableDataServiceBackend):
    def table_schema(self, table_key, schema_cb, failure_cb):
        schema_cb(pa.schema([pa.field("id", pa.int64())]), None)
    
    def table_locations(self, table_key, location_cb, success_cb, failure_cb):
        location_cb(TestLocationKey("loc1"), None)
        success_cb()
    
    def subscribe_to_table_locations(self, table_key, location_cb, success_cb, failure_cb):
        location_cb(TestLocationKey("loc1"), None)  # ❌ Fails here
        success_cb()
        return lambda: None
    
    def table_location_size(self, table_key, table_location_key, size_cb, failure_cb):
        size_cb(0)
    
    def subscribe_to_table_location_size(self, table_key, table_location_key, 
                                         size_cb, success_cb, failure_cb):
        size_cb(0)
        success_cb()
        return lambda: None
    
    def column_values(self, table_key, table_location_key, col, offset, 
                      min_rows, max_rows, values_cb, failure_cb):
        values_cb(pa.table({col: pa.array([], type=pa.int64())}))

# This fails with AttributeError
backend = MinimalBackend()
service = TableDataService(backend)
table = service.make_table(TestKey("test"), refreshing=True)
```

## Expected vs Actual Behavior

**Expected** (per [official documentation](https://deephaven.io/core/docs/how-to-guides/data-import-export/table-data-service/)):
```python
# Documentation shows direct callback invocation
def subscribe_to_table_locations(self, table_key, location_cb, success_cb, failure_cb):
    for key, location in table.locations.items():
        location_cb(key, location.partitioning_values)  # Direct call ✓
    success_cb()
    return unsubscribe_fn
```

**Actual** (in library code):
```python
# Library tries to call .apply() method
location_cb.apply(j_tbl_location_key, jpy.array("java.nio.ByteBuffer", []))
# But callback object doesn't have .apply() method
```

## Root Cause

Mismatch between:
1. **Python backend implementations**: Call callbacks directly as functions (per docs)
2. **Library's callback proxy**: Tries to call `.apply()` and `.accept()` methods
3. **Java lambda objects**: Don't have these methods

## Impact

- ✅ Both `table_locations()` and `subscribe_to_table_locations()` fail
- ✅ Cannot create static tables
- ✅ Cannot create refreshing tables
- ✅ **Entire API is non-functional**

## Suggested Fix

**Option 1** - Change proxy to use direct invocation:
```python
# Current (broken)
location_cb.apply(j_tbl_location_key, jpy.array("java.nio.ByteBuffer", []))

# Proposed fix
location_cb(j_tbl_location_key, jpy.array("java.nio.ByteBuffer", []))
```

**Option 2** - Fix Java-side to provide callable with `.apply()` method

**Option 3** - Update documentation to match actual implementation

## Test Evidence

Tested in both v0.40.7 and edge build with identical results. Test script available at:
- Repository: `data/deephaven/notebooks/test_tds_v0407.py`
- Command: `exec(open('/data/notebooks/test_tds_v0407.py').read())`

## Use Case

Integrating QuestDB as a time-series backend for Deephaven with WAL-driven real-time streaming. TableDataService would enable:
- Zero-copy data access from QuestDB
- Real-time updates via Write-Ahead Log monitoring
- Memory-efficient columnar storage with Deephaven's compute layer

## Workaround

None available - bug is in library code. Currently using direct PostgreSQL queries with polling as alternative.

## Request

Please investigate and fix the callback proxy implementation. This API would be valuable for integrating external data sources with real-time updates, but is currently unusable.

## Related Files

- Test script: `data/deephaven/notebooks/test_tds_v0407.py`
- Implementation attempt: `data/deephaven/notebooks/trades_live_wal.py`
- Detailed analysis: `V0407_BUG_CONFIRMED.md`

---

**Priority**: High - API completely non-functional in all tested versions  
**Status**: Experimental API, present in v0.40.7+, never worked  
**Workaround**: None - requires library fix
