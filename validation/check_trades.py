#!/usr/bin/env python3
import sys
sys.path.insert(0, 'docker/cryptofeed/src')
from questdb_writer import QuestDBWriter
from datetime import datetime

writer = QuestDBWriter('localhost')

# Query latest trades per symbol
result = writer.execute_sql('''
    SELECT symbol, timestamp, exchange, side, price, size 
    FROM trades 
    LATEST ON timestamp PARTITION BY symbol
''')

print('Latest trades per symbol:')
print('=' * 100)

rows = result.get('dataset', [])
if rows:
    for row in rows:
        symbol = row[0]
        ts = row[1]
        exchange = row[2]
        side = row[3]
        price = row[4]
        size = row[5]
        print(f'{symbol:12s} | {ts} | {exchange:10s} | {side:4s} | ${price:,.2f} x {size:.6f}')
else:
    print("No trades found!")

# Count total trades
result2 = writer.execute_sql('SELECT count() FROM trades')
total = result2.get('dataset', [[0]])[0][0]
print(f'\nTotal trades: {total}')

writer.close()
