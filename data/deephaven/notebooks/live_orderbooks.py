"""
Deephaven notebook: Real-time Orderbooks from QuestDB

This notebook reads orderbook data from QuestDB materialized view 
(1-second snapshots) and creates Deephaven tables for analysis.

Tables created:
- orderbooks: Latest orderbook snapshots (1-second resolution)
- orderbooks_btc: Bitcoin orderbooks only
- orderbooks_latest: Latest snapshot per exchange
- orderbooks_spread: Bid-ask spread analysis
"""

import qdb
from deephaven import agg

# Load recent orderbook snapshots from QuestDB
print("Loading orderbook snapshots from QuestDB...")
orderbooks = qdb.get_orderbooks(last_n=5000)
print(f"✅ Loaded {orderbooks.size} orderbook snapshots")


# Create filtered tables
orderbooks_btc = orderbooks.where("symbol == `BTC-USD`")
orderbooks_eth = orderbooks.where("symbol == `ETH-USD`")


# Create spread analysis table
from deephaven import agg

orderbooks_spread = orderbooks.view([
    "exchange",
    "symbol",
    "spread",
    "spreadBps = (Spread / MidPrice) * 10000",  # Spread in basis points
    "MidPrice"
]).agg_by([
    agg.avg("AvgSpread = Spread"),
    agg.avg("AvgSpreadBps = SpreadBps"),
    agg.min_("MinSpread = Spread"),
    agg.max_("MaxSpread = Spread"),
    agg.avg("AvgMidPrice = MidPrice"),
    agg.count_("SnapshotCount")
], by=["Exchange", "Symbol"])


# Time-based aggregations (5-second bins)
orderbooks_5s = orderbooks.update_view([
    "TimeBin = lowerBin(Timestamp, 5_000_000_000L)"  # 5 seconds in nanoseconds
]).agg_by([
    agg.avg("AvgBestBid = BestBid"),
    agg.avg("AvgBestAsk = BestAsk"),
    agg.avg("AvgSpread = Spread"),
    agg.avg("AvgMidPrice = MidPrice"),
    agg.count_("SnapshotCount")
], by=["Exchange", "Symbol", "TimeBin"])


# BTC-specific 5-second aggregation
btc_5s = orderbooks_5s.where("Symbol == 'BTC-USD'")


# Latest snapshot per exchange/symbol
orderbooks_latest = orderbooks.last_by(["Exchange", "Symbol"])

