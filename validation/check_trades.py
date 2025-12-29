#!/usr/bin/env python3
import sys

sys.path.insert(0, "docker/cryptofeed/src")
from questdb_writer import QuestDBWriter
from datetime import datetime

writer = QuestDBWriter("localhost")

# Query latest trades per symbol
result = writer.execute_sql("""
    SELECT symbol, timestamp, exchange, side, price, size 
    FROM trades 
    LATEST ON timestamp PARTITION BY symbol
""")

print("Latest trades per symbol:")
print("=" * 100)

rows = result.get("dataset", [])
if rows:
    for row in rows:
        symbol = row[0]
        ts = row[1]
        exchange = row[2]
        side = row[3]
        price = row[4]
        size = row[5]
        print(f"{symbol:12s} | {ts} | {exchange:10s} | {side:4s} | ${price:,.2f} x {size:.6f}")
else:
    print("No trades found!")

# Count total trades
result2 = writer.execute_sql("SELECT count() FROM trades")
total = result2.get("dataset", [[0]])[0][0]
print(f"\nTotal trades: {total}")

writer.close()


import polars as pl

QUESTDB_URI = "redshift://admin:quest@localhost:8812/qdb"
QUERY = "SELECT * FROM trades LIMIT 5;"

df = pl.read_database_uri(query=QUERY, uri=QUESTDB_URI).to_pandas(use_pyarrow_extension_array=True)
print("Received DataFrame:")
print(df)


import pandas
import pyarrow as pa

import psycopg2


def get_connection():
    return psycopg2.connect(
        user="admin",
        password="quest",
        host="localhost",  # Docker Compose service name
        port="8812",
        database="qdb",
    )


with get_connection() as conn:
    query = "SELECT * FROM orderbooks LIMIT 5;"
    df = pd.read_sql_query(query, conn)
    table = pa.Table.from_pandas(df)
