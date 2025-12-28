"""
Deephaven notebook: Real-time Trades from QuestDB

This notebook reads trade data from QuestDB and creates Deephaven tables
for analysis and visualization.

Tables created:
- trades: All recent trades
- trades_btc: Bitcoin trades only
- trades_summary: Trade count by exchange and symbol
- candles_1m: 1-minute OHLCV candles
"""

import qdb
from deephaven import agg
import importlib

importlib.reload(qdb)


trades = qdb.get_trades(last_nticks=10)


# # Create filtered tables
# trades_btc = trades.where("symbol == `BTC-USD`")
# trades_eth = trades.where("symbol == `ETH-USD`")


# # Create summary tables
# trades_summary = trades.agg_by(
#     [
#         agg.count_("TradeCount"),
#         agg.avg("AvgPrice = price"),
#         agg.sum_("TotalVolume = size"),
#         agg.min_("MinPrice = price"),
#         agg.max_("MaxPrice = price"),
#     ],
#     by=["exchange", "symbol"],
# )


# # Get 1-minute candles from QuestDB
# candles_1m = qdb.get_candles(sample_by="1m")
# btc_1m = candles_1m.where("symbol == `BTC-USD`")
