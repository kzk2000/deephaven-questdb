#!/usr/bin/env python3
"""
Complete patch for TableDataService API - Fix ALL Java callback invocations

Java functional interfaces need specific method calls:
  - BiConsumer<T,U>: callback.accept(t, u)
  - Consumer<T>: callback.accept(t)
  - Runnable: callback.run()
  - LongConsumer: callback.accept(long)
"""

import os
import sys
import re

FILE_PATH = (
    "/opt/deephaven/venv/lib/python3.10/site-packages/deephaven/experimental/table_data_service.py"
)
BACKUP_PATH = FILE_PATH + ".backup"


def create_backup():
    if not os.path.exists(BACKUP_PATH):
        with open(FILE_PATH, "r") as src:
            with open(BACKUP_PATH, "w") as dst:
                dst.write(src.read())
        print(f"[OK] Created backup: {BACKUP_PATH}")
    else:
        print(f"[OK] Backup already exists: {BACKUP_PATH}")


def patch_file():
    with open(FILE_PATH, "r") as f:
        content = f.read()

    original = content
    patches = []

    # 1. Fix ALL location_cb.apply() -> location_cb.accept()
    before_count = content.count("location_cb.apply")
    if before_count > 0:
        content = content.replace("location_cb.apply", "location_cb.accept")
        patches.append(f"location_cb.apply() -> location_cb.accept() ({before_count} occurrences)")
    else:
        patches.append("location_cb.accept() - already fixed")

    # 2. Fix success_cb() -> success_cb.run()
    # In success_cb_proxy and other places
    pattern1 = re.compile(r"\bsuccess_cb\(\)")
    matches = pattern1.findall(content)
    if matches:
        content = pattern1.sub("success_cb.run()", content)
        patches.append(f"success_cb() -> success_cb.run() ({len(matches)} occurrences)")

    # 3. Fix failure_cb(msg) -> failure_cb.accept(msg)
    # Look for failure_cb("...") or failure_cb(var)
    pattern2 = re.compile(r"\bfailure_cb\(([^)]+)\)")
    matches = pattern2.findall(content)
    if matches:
        content = pattern2.sub(r"failure_cb.accept(\1)", content)
        patches.append(f"failure_cb(x) -> failure_cb.accept(x) ({len(matches)} occurrences)")

    # 4. Fix size_cb(num) -> size_cb.accept(num) for LongConsumer
    pattern3 = re.compile(r"\bsize_cb\(([^)]+)\)")
    matches = pattern3.findall(content)
    if matches:
        content = pattern3.sub(r"size_cb.accept(\1)", content)
        patches.append(f"size_cb(x) -> size_cb.accept(x) ({len(matches)} occurrences)")

    # 5. Fix schema_cb(schema, table) - this is BiConsumer
    # But it already has correct invocation in the library, check anyway
    if "schema_cb(" in content and "schema_cb.accept(" not in content:
        pattern4 = re.compile(r"\bschema_cb\(([^)]+)\)")
        matches = pattern4.findall(content)
        if matches:
            content = pattern4.sub(r"schema_cb.accept(\1)", content)
            patches.append(f"schema_cb(x) -> schema_cb.accept(x) ({len(matches)} occurrences)")

    # 6. Fix values_cb(table) - Consumer
    pattern5 = re.compile(r"\bvalues_cb\(([^)]+)\)")
    matches = pattern5.findall(content)
    if matches:
        content = pattern5.sub(r"values_cb.accept(\1)", content)
        patches.append(f"values_cb(x) -> values_cb.accept(x) ({len(matches)} occurrences)")

    if content == original:
        print("[WARN] No changes made")
        return False

    with open(FILE_PATH, "w") as f:
        f.write(content)

    print(f"\n[OK] Applied patches:")
    for p in patches:
        print(f"  - {p}")

    return True


def verify():
    with open(FILE_PATH, "r") as f:
        content = f.read()

    issues = []

    # Check that callbacks use correct methods
    if re.search(r"location_cb\(j_tbl_location_key", content):
        if not re.search(r"location_cb\.accept\(j_tbl_location_key", content):
            issues.append("location_cb() without .accept()")

    # Verify fixes
    fixes = []
    if "success_cb.run()" in content:
        fixes.append("success_cb.run() ✓")
    if "failure_cb.accept(" in content:
        fixes.append("failure_cb.accept() ✓")
    if "size_cb.accept(" in content:
        fixes.append("size_cb.accept() ✓")
    if "location_cb.accept(" in content:
        fixes.append("location_cb.accept() ✓")

    if issues:
        print("\n[FAIL] Verification issues:")
        for i in issues:
            print(f"  - {i}")
        return False
    else:
        print("\n[OK] Verification passed:")
        for f in fixes:
            print(f"  - {f}")
        return True


def main():
    print("=" * 70)
    print("TableDataService Complete Callback Patch")
    print("=" * 70)
    print("Fixes ALL Java functional interface invocations")
    print()

    if not os.path.exists(FILE_PATH):
        print(f"[FAIL] File not found: {FILE_PATH}")
        sys.exit(1)

    create_backup()

    print("\nApplying patches...")
    if patch_file():
        if verify():
            print("\n" + "=" * 70)
            print("[SUCCESS] ALL CALLBACKS PATCHED")
            print("=" * 70)
            print("\nRestart Deephaven:")
            print("  docker-compose restart deephaven")
            print("\nThen test:")
            print("  exec(open('/data/storage/notebooks/test_location_cb.py').read())")
        else:
            print("\n[FAIL] Verification failed")
            sys.exit(1)
    else:
        print("\n[WARN] No patches applied")


if __name__ == "__main__":
    main()
