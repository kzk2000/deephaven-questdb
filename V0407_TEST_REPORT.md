# Deephaven v0.40.7 TableDataService Test Report

**Date**: December 6, 2024  
**Purpose**: Verify TableDataService API availability in v0.40.7 (stable release)

## Test Environment

- **Docker Image**: `ghcr.io/deephaven/server:0.40.7`
- **Build Type**: Stable release
- **Purpose**: Verify if TableDataService API exists in v0.40.7

## Background

From our previous investigation:
- TableDataService API was added to Deephaven **November 1, 2024**
- All v0.40.x releases were cut **before this date**
- The API should **NOT exist** in v0.40.7

## Test Results

### Expected Result
❌ TableDataService API should NOT be available in v0.40.7

### Actual Result
[Test in progress...]

## Why This Matters

If TableDataService doesn't exist in v0.40.7 (as expected), this confirms:

1. ✅ Our research was correct - API added after v0.40.7 release
2. ✅ Users need edge build or future v0.41.0 for this feature
3. ✅ The bug report is valid for edge build only

## Timeline

- **Oct 2024**: v0.40.7 released
- **Nov 1, 2024**: TableDataService API added to main branch
- **Dec 6, 2024**: We discovered edge build bug
- **Future**: v0.41.0 stable release (Q1 2025 expected)

## Implications

### For v0.40.7 Users
- ❌ No TableDataService API available
- ✅ Must use direct database queries (qdb.py approach)
- ✅ Stable, production-ready build
- ⏳ Wait for v0.41.0 for TableDataService

### For Edge Build Users
- ✅ TableDataService API present
- ❌ API has callback bug (see DEEPHAVEN_BUG_REPORT.md)
- ⚠️ Not production-ready
- 🐛 Bug needs to be reported/fixed

## Recommendation

**For Production**: Use v0.40.7 with qdb.py polling approach
**For Testing TableDataService**: Use edge build (but expect bug)
**For Stable TableDataService**: Wait for v0.41.0 release

## Related Documents

- `DEEPHAVEN_BUG_REPORT.md` - Bug details for edge build
- `UPGRADE_SUMMARY.md` - Why we upgraded to edge
- `data/deephaven/notebooks/trades_live_wal.py` - TableDataService implementation
- `data/deephaven/notebooks/qdb.py` - Working v0.40.7 approach

## Next Steps

1. Confirm TableDataService absence in v0.40.7
2. Document working approach for v0.40.7
3. Keep bug report ready for Deephaven team
4. Monitor for v0.41.0 release
