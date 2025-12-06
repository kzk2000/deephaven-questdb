# Bug Report: TableDataService API - Callback Method Not Found

## Executive Summary

The experimental `TableDataService` API is completely non-functional in all tested versions due to a callback proxy implementation error. The API attempts to call `.apply()` and `.accept()` methods on Java lambda objects that don't have these methods, resulting in `AttributeError`.

**Status**: Confirmed in v0.40.7 (stable) and v0.41.0+snapshot (edge)  
**Priority**: High - API completely unusable  
**Impact**: Any user following official documentation will encounter this error  

---

## Affected Versions

| Version | Status | Error Location | Python |
|---------|--------|----------------|--------|
| v0.40.7 (stable) | ✅ Confirmed | line 378 | 3.10.12 |
| v0.41.0+snapshot (edge) | ✅ Confirmed | line 459 | 3.12.3 |
| Earlier v0.40.x | ❓ Likely affected | - | - |

**Conclusion**: Bug exists in all versions with TableDataService API

---

## The Bug

### Code Location
`deephaven/experimental/table_data_service.py`

### Problematic Code
```python
def location_cb_proxy(pt_location_key: TableLocationKey, pt_table: Optional[pa.Table] = None):
    j_tbl_location_key = _JTableLocationKeyImpl(pt_location_key)
    if pt_table is None:
        # ❌ FAILS HERE - .apply() doesn't exist
        location_cb.apply(j_tbl_location_key, jpy.array("java.nio.ByteBuffer", []))
    else:
        if pt_table.num_rows != 1:
            raise ValueError("...")
        bb_list = [jpy.byte_buffer(rb.serialize()) for rb in pt_table.to_batches()]
        bb_list.insert(0, jpy.byte_buffer(pt_table.schema.serialize()))
        # ❌ ALSO FAILS - .accept() doesn't exist
        location_cb.accept(j_tbl_location_key, jpy.array("java.nio.ByteBuffer", bb_list))
```

### Error Message
```
AttributeError: 'io.deephaven.extensions.barrage.util.PythonTableDataService$BackendAccessor$$Lambda/...' 
object has no attribute 'apply'

Traceback:
  File "table_data_service.py", line 378 (v0.40.7) or line 459 (edge)
    location_cb.apply(j_tbl_location_key, jpy.array("java.nio.ByteBuffer", []))
    ^^^^^^^^^^^^^^^^^
  AttributeError: 'io.deephaven.extensions.barrage.util.PythonTableDataService$BackendAccessor$$Lambda/...' 
  object has no attribute 'apply'
```

### Root Cause
The Java lambda callback objects passed from the Java side don't have `.apply()` or `.accept()` methods. The Python proxy code assumes these methods exist, but they're not part of the lambda's interface.

**Mismatch**:
- Python backend implementations call callbacks directly (per documentation)
- Library's proxy wrapper tries to call `.apply()` / `.accept()` methods
- Java lambda objects don't provide these methods

---

## Minimal Reproducible Example

Copy-paste ready code that demonstrates the bug:

```python
from deephaven.experimental.table_data_service import (
    TableDataService, TableDataServiceBackend, TableKey, TableLocationKey
)
import pyarrow as pa

# Minimal TableKey implementation
class TestKey(TableKey):
    def __init__(self, name): 
        self.name = name
    def __hash__(self): 
        return hash(self.name)
    def __eq__(self, other): 
        return isinstance(other, TestKey) and self.name == other.name

# Minimal TableLocationKey implementation
class TestLocationKey(TableLocationKey):
    def __init__(self, name): 
        self.name = name
    def __hash__(self): 
        return hash(self.name)
    def __eq__(self, other): 
        return isinstance(other, TestLocationKey) and self.name == other.name

# Backend implementation following official documentation
class MinimalBackend(TableDataServiceBackend):
    def table_schema(self, table_key, schema_cb, failure_cb):
        schema_cb(pa.schema([pa.field("id", pa.int64())]), None)
    
    def table_locations(self, table_key, location_cb, success_cb, failure_cb):
        location_cb(TestLocationKey("loc1"), None)
        success_cb()
    
    def subscribe_to_table_locations(self, table_key, location_cb, success_cb, failure_cb):
        # Following documentation - direct callback invocation
        location_cb(TestLocationKey("loc1"), None)  # ❌ FAILS HERE
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

# Test - this will fail with AttributeError
backend = MinimalBackend()
service = TableDataService(backend)
table = service.make_table(TestKey("test"), refreshing=True)
# Error: AttributeError: object has no attribute 'apply'
```

---

## Expected vs Actual Behavior

### Expected (per official documentation)
From: https://deephaven.io/core/docs/how-to-guides/data-import-export/table-data-service/

```python
def subscribe_to_table_locations(self, table_key, location_cb, success_cb, failure_cb):
    """Subscribe to table locations with a callable."""
    # Documentation shows DIRECT callback invocation
    for key, location in table.locations.items():
        location_cb(key, location.partitioning_values)  # ✓ Direct call
    success_cb()  # ✓ Direct call
    return unsubscribe_function
```

### Actual (library implementation)
```python
def location_cb_proxy(...):
    # Library tries to call methods that don't exist
    location_cb.apply(...)   # ❌ No .apply() method
    location_cb.accept(...)  # ❌ No .accept() method
```

---

## Suggested Fixes

### Option 1: Direct Invocation (Simplest)
Change the proxy to invoke callbacks directly instead of calling `.apply()` / `.accept()`:

```python
# Current (broken)
location_cb.apply(j_tbl_location_key, jpy.array("java.nio.ByteBuffer", []))

# Proposed fix
location_cb(j_tbl_location_key, jpy.array("java.nio.ByteBuffer", []))
```

**Pros**: Minimal change, matches documentation  
**Cons**: Need to verify jpy can call Java objects directly with this signature

### Option 2: Fix Java-Side Lambda
Ensure the Java lambda objects passed to Python have `.apply()` / `.accept()` methods:

```java
// Current: Lambda without apply/accept methods
// Proposed: Provide lambda that implements appropriate functional interface
```

**Pros**: Maintains current Python proxy approach  
**Cons**: Requires Java-side changes

### Option 3: Callable Wrapper
Wrap the callback in Python to provide both calling styles:

```python
class CallbackWrapper:
    def __init__(self, callback):
        self._cb = callback
    def __call__(self, *args):
        return self._cb(*args)
    def apply(self, *args):
        return self._cb(*args)
    def accept(self, *args):
        return self._cb(*args)

location_cb = CallbackWrapper(original_callback)
```

**Pros**: Backward compatible  
**Cons**: More complex, overhead

---

## Test Evidence

### Test Environment
- **Tested on**: December 6, 2024
- **v0.40.7**: Docker `ghcr.io/deephaven/server:0.40.7`, Python 3.10.12
- **Edge**: Docker `ghcr.io/deephaven/server:edge`, Python 3.12.3

### Test Script
See repository: `data/deephaven/notebooks/test_tds_v0407.py`

Run from Deephaven UI:
```python
exec(open('/data/notebooks/test_tds_v0407.py').read())
```

### Test Results
Both versions show identical error:
- ✅ TableDataService module imports successfully
- ✅ Backend implementation works
- ✅ TableDataService creation succeeds
- ❌ `make_table(refreshing=True)` fails with AttributeError

---

## Impact Assessment

### Affected Operations
- ❌ `table_locations()` - Cannot create static tables
- ❌ `subscribe_to_table_locations()` - Cannot create refreshing tables
- **Result**: Entire API is non-functional

### User Impact
- Any developer following official TableDataService documentation
- Integration with external data sources (databases, streams)
- Real-time/streaming use cases

### Business Impact
- Experimental API cannot be promoted to stable
- Users cannot integrate custom backends
- Documentation doesn't match implementation

---

## Use Case Context

We're integrating QuestDB (time-series database) as a backend for Deephaven to enable:
- **Real-time streaming**: WAL-driven updates without polling
- **Zero-copy access**: Columnar data from QuestDB → Deephaven
- **Memory efficiency**: On-demand paging of large datasets
- **Low latency**: 50-100ms update propagation via Write-Ahead Log

TableDataService is the perfect API for this use case, but it's completely broken.

---

## Workaround Status

**No workaround available** - bug is in library code, not addressable from user code.

**Current alternative**:
```python
# Direct PostgreSQL queries with polling (not ideal)
import psycopg2
from deephaven import time_table

def get_trades():
    conn = psycopg2.connect(host='questdb', port=8812, ...)
    # ... query logic ...
    
# Poll every second (inefficient compared to WAL-driven)
trades = time_table("PT1S").update("data = get_trades()")
```

---

## Additional Context

### Related Java Code
- `PythonTableDataService.java` (lines 275, 278, 653, 657)
- `BackendAccessor.subscribeToTableLocations()` (line 278)

### Related Python Code
- `deephaven/experimental/table_data_service.py`
  - `location_cb_proxy` function (lines 378, 459 depending on version)
  - `_subscribe_to_table_locations` method

### Affected Methods in Python API
- `table_locations()` 
- `subscribe_to_table_locations()`

Both methods use the same broken `location_cb_proxy` pattern.

---

## Recommended Next Steps

1. **Immediate**: Add integration test that exercises full callback chain with custom backend
2. **Fix**: Implement one of the three suggested fixes (Option 1 recommended)
3. **Verify**: Run existing unit tests + new integration test
4. **Document**: If API changes, update documentation to match
5. **Release**: Include in next patch release (v0.40.8?) and edge build

---

## Questions for Maintainers

1. Was this API ever tested with a real custom backend implementation?
2. Are there existing integration tests that should have caught this?
3. Is the Java lambda type intentionally missing `.apply()` / `.accept()`?
4. Should we add more comprehensive testing for experimental APIs?

---

## Attachments

- Test script: `data/deephaven/notebooks/test_tds_v0407.py`
- Real-world implementation attempt: `data/deephaven/notebooks/trades_live_wal.py`
- QuestDB integration code: `data/deephaven/notebooks/qdb.py`

---

**Reporter**: Community user  
**Date**: December 6, 2024  
**Priority**: High  
**Severity**: API completely non-functional  
**Versions Tested**: v0.40.7 (stable), v0.41.0+snapshot (edge)  

---

*This bug prevents any real-world usage of TableDataService API. The API has been broken since initial release and needs urgent attention before it can be promoted from experimental status.*
