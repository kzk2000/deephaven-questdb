# Cleanup Old Test Files

## Overview

Test files have been reorganized into `docker/cryptofeed/tests/`. The old test files in the project root can now be removed.

## Files to Remove

### Test Files (moved to `docker/cryptofeed/tests/`)

| Old Location (root) | New Location | Status |
|---------------------|--------------|--------|
| `test_imports.py` | `tests/unit/test_imports.py` | ✅ Migrated |
| `test_orderbook_writer.py` | `tests/integration/test_orderbooks.py` | ✅ Migrated |
| `test_live_simulation.py` | `tests/simulation/test_live_feed.py` | ✅ Migrated |
| `check_trades.py` | `tests/utils/check_trades.py` | ✅ Migrated |
| `verify_data.py` | `tests/utils/verify_data.py` | ✅ Migrated |
| `final_status.py` | `tests/utils/system_status.py` | ✅ Migrated |

### Utility Files (can be removed)

| File | Reason | Alternative |
|------|--------|-------------|
| `drop_old_tables.py` | One-time migration script | Tables auto-managed by tests |
| `test_and_docker_restart.py` | Simple restart script | Use `make restart` |

### Documentation Files (can be archived or removed)

| File | Reason |
|------|--------|
| `END_TO_END_VERIFICATION.md` | One-time verification notes |
| `FINAL_STATUS.md` | Migration status notes |
| `REFACTORING_COMPLETE.md` | Migration notes |
| `REFACTORING_SUMMARY.md` | Migration notes |
| `UNIFICATION_COMPLETE.md` | Migration notes |
| `UNIFICATION_FIX.md` | Migration notes |
| `UNIFICATION_SUCCESS.md` | Migration notes |

## Cleanup Commands

### Safe Cleanup (move to archive)

```bash
# Create archive directory
mkdir -p archive/migration_docs
mkdir -p archive/old_tests

# Move old test files
mv test_*.py archive/old_tests/
mv check_trades.py verify_data.py final_status.py archive/old_tests/
mv drop_old_tables.py test_and_docker_restart.py archive/old_tests/

# Move migration documentation
mv END_TO_END_VERIFICATION.md archive/migration_docs/
mv FINAL_STATUS.md archive/migration_docs/
mv REFACTORING_*.md archive/migration_docs/
mv UNIFICATION_*.md archive/migration_docs/

echo "✅ Old files archived"
```

### Complete Removal (if you're confident)

```bash
# Remove old test files
rm -f test_imports.py
rm -f test_orderbook_writer.py
rm -f test_live_simulation.py
rm -f check_trades.py
rm -f verify_data.py
rm -f final_status.py
rm -f drop_old_tables.py
rm -f test_and_docker_restart.py

# Remove migration documentation
rm -f END_TO_END_VERIFICATION.md
rm -f FINAL_STATUS.md
rm -f REFACTORING_COMPLETE.md
rm -f REFACTORING_SUMMARY.md
rm -f UNIFICATION_COMPLETE.md
rm -f UNIFICATION_FIX.md
rm -f UNIFICATION_SUCCESS.md

echo "✅ Old files removed"
```

## Verification

After cleanup, verify the new test structure works:

```bash
# Run unit tests
cd docker/cryptofeed/tests
./run_tests.sh unit

# Run utility scripts
python utils/check_trades.py
python utils/system_status.py

# Everything should work!
```

## What to Keep

### Essential Files (DO NOT REMOVE)

- `README.md` - Project documentation
- `README_ORDERBOOKS.md` - Orderbook implementation notes
- `Makefile` - Build and test automation
- `docker-compose.yml` - Container orchestration
- `LICENSE` - Project license
- `.gitignore` - Git configuration
- `FINAL_SUMMARY.md` - Unification summary
- `CLEANUP_OLD_TESTS.md` - This file

### New Test Structure (KEEP)

- `docker/cryptofeed/tests/` - Entire test directory

## Benefits of Cleanup

1. **Cleaner root directory** - Only essential files
2. **No confusion** - Tests in one location
3. **Better organization** - Clear structure
4. **Easier navigation** - Logical grouping
5. **Reduced clutter** - Remove migration artifacts

## Recommendation

**Use the "Safe Cleanup" approach first:**
1. Archive old files instead of deleting
2. Verify new tests work
3. Use the system for a few days
4. If confident, delete the archive

This way you have a backup if needed!
