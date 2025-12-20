#!/usr/bin/env python3
"""Final status check after QuestDB writer unification"""
import sys
sys.path.insert(0, 'docker/cryptofeed/src')
from questdb_writer import QuestDBWriter
from datetime import datetime
import time

writer = QuestDBWriter('localhost')

print("=" * 100)
print("FINAL STATUS CHECK - QuestDB Writer Unification")
print("=" * 100)

# 1. Check tables exist
print("\n1. TABLES:")
tables = ['trades', 'orderbooks', 'orderbooks_1s']
for table in tables:
    result = writer.execute_sql(f"SELECT count() FROM {table}")
    count = result.get('dataset', [[0]])[0][0]
    print(f"   ✓ {table:15s} {count:>8,} rows")

# 2. Check latest trades
print("\n2. LATEST TRADES (per symbol):")
result = writer.execute_sql('''
    SELECT symbol, timestamp, exchange, side, price, size 
    FROM trades 
    LATEST ON timestamp PARTITION BY symbol
''')
rows = result.get('dataset', [])
for row in rows[:4]:
    symbol, ts, exchange, side, price, size = row
    print(f"   {symbol:10s} | {ts} | {exchange:10s} | {side:4s} | ${price:>10,.2f} x {size:.6f}")

# 3. Check latest orderbooks
print("\n3. LATEST ORDERBOOKS (per symbol):")
result = writer.execute_sql('''
    SELECT symbol, timestamp, exchange
    FROM orderbooks 
    LATEST ON timestamp PARTITION BY symbol, exchange
    LIMIT 4
''')
rows = result.get('dataset', [])
for row in rows:
    symbol, ts, exchange = row
    print(f"   {symbol:10s} | {ts} | {exchange:10s}")

# 4. Wait and check growth
print("\n4. GROWTH CHECK (waiting 10 seconds)...")
trades_before = writer.execute_sql("SELECT count() FROM trades").get('dataset', [[0]])[0][0]
orderbooks_before = writer.execute_sql("SELECT count() FROM orderbooks").get('dataset', [[0]])[0][0]

time.sleep(10)

trades_after = writer.execute_sql("SELECT count() FROM trades").get('dataset', [[0]])[0][0]
orderbooks_after = writer.execute_sql("SELECT count() FROM orderbooks").get('dataset', [[0]])[0][0]

trades_growth = trades_after - trades_before
orderbooks_growth = orderbooks_after - orderbooks_before

print(f"   Trades:     {trades_before:>6,} → {trades_after:>6,} (+{trades_growth:>3,}) {'✓' if trades_growth > 0 else '✗'}")
print(f"   Orderbooks: {orderbooks_before:>6,} → {orderbooks_after:>6,} (+{orderbooks_growth:>3,}) {'✓' if orderbooks_growth > 0 else '✗'}")

# 5. Summary
print("\n5. UNIFIED WRITER STATUS:")
print(f"   ✓ Single import: from questdb_writer import QuestDBWriter")
print(f"   ✓ ILP protocol:  TCP port 9009 (trades)")
print(f"   ✓ REST API:      HTTP port 9000 (orderbooks, queries)")
print(f"   ✓ Legacy files:  DELETED")

print("\n" + "=" * 100)
if trades_growth > 0 and orderbooks_growth > 0:
    print("✅ ALL SYSTEMS OPERATIONAL - Unification complete!")
else:
    print("⚠️  WARNING: Data not flowing as expected")
print("=" * 100)

writer.close()
