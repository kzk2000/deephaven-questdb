"""
Deephaven notebook: Real-time Orderbooks from QuestDB

This notebook reads orderbook data from QuestDB materialized view
(1-second snapshots) and creates Deephaven tables for analysis.

Data source: orderbooks_1s (materialized view with 1s sampling)

Tables created:
- orderbooks: Full orderbook snapshots with 2D arrays (1-second resolution)
- orderbooks_btc: Bitcoin orderbooks only
- orderbooks_latest: Latest snapshot per exchange/symbol

Note: Uses JPY to convert Python lists to Java Double[][] arrays
Each orderbook has bids/asks as 2D arrays: [[prices...], [volumes...]]
"""

import qdb
import importlib

importlib.reload(qdb)


# Load recent orderbook snapshots from QuestDB with full 2D array data
orderbooks = qdb.get_orderbooks(last_n=10)

# Create filtered tables
orderbooks_btc = orderbooks.where("symbol == `BTC-USD`")

# Latest snapshot per exchange/symbol
# orderbooks_latest = orderbooks.last_by(["exchange", "symbol"])
