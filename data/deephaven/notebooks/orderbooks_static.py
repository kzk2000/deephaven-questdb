"""
Deephaven notebook: Real-time Orderbooks from QuestDB

This notebook reads orderbook data from QuestDB materialized view 
(1-second snapshots) and creates Deephaven tables for analysis.

Data source: orderbooks_1s (materialized view with 1s sampling)

Tables created:
- orderbooks: Latest orderbook snapshots (1-second resolution)
- orderbooks_btc: Bitcoin orderbooks only
- orderbooks_eth: Ethereum orderbooks only
- orderbooks_latest: Latest snapshot per exchange/symbol

Note: The orderbooks contain JSON arrays of [price, size] for bids/asks
"""

import qdb
from deephaven import agg

# Load recent orderbook snapshots from QuestDB
print("Loading orderbook snapshots from QuestDB...")
orderbooks = qdb.get_orderbooks(last_n=5000)
print(f"✅ Loaded {orderbooks.size} orderbook snapshots")


# Create filtered tables
orderbooks_btc = orderbooks.where("symbol == `BTC-USD`")

# Latest snapshot per exchange/symbol
orderbooks_latest = orderbooks.last_by(["exchange", "symbol"])


