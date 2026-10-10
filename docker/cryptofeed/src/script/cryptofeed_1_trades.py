import os

import src.cryptofeed_tools as cft
from cryptofeed import FeedHandler
from cryptofeed.defines import TRADES
from cryptofeed.exchanges import Bitstamp, Coinbase, Kraken
from src.questdb_writer import QuestDBWriter

# Initialize QuestDB writer
questdb_host = "questdb" if os.environ.get("IS_DOCKER") else "127.0.0.1"
questdb_writer = QuestDBWriter(host=questdb_host)
# drop replays (Coinbase re-sends its last match on subscribe) of trades already stored
print(f"seeded {questdb_writer.seed_seen_trades()} recent trade ids")


async def write_to_questdb(data, receipt_timestamp):
    """Callback to write trade data to QuestDB"""
    # Convert trade object to dict if needed
    if hasattr(data, "to_dict"):
        trade_data = data.to_dict(numeric_type=float, none_to=None)
    else:
        trade_data = data

    # Add receipt timestamp if not present
    if "receipt_timestamp" not in trade_data:
        trade_data["receipt_timestamp"] = receipt_timestamp
    if not trade_data.get("timestamp"):
        trade_data["timestamp"] = receipt_timestamp

    # Write to QuestDB
    questdb_writer.write_trade(trade_data)


def main():
    # cft.SYMBOLS = ['BTC-USD']   # for testing

    f = FeedHandler()
    # Write directly to QuestDB using custom writer
    f.add_feed(
        Coinbase(
            channels=[TRADES],
            symbols=cft.SYMBOLS,
            callbacks={TRADES: [write_to_questdb, *cft.debug_callbacks()]},
        )
    )
    f.add_feed(
        Bitstamp(
            channels=[TRADES],
            symbols=cft.SYMBOLS,
            callbacks={TRADES: [write_to_questdb, *cft.debug_callbacks()]},
        )
    )
    f.add_feed(
        Kraken(
            channels=[TRADES],
            symbols=cft.SYMBOLS,
            callbacks={TRADES: [write_to_questdb, *cft.debug_callbacks()]},
        )
    )

    try:
        f.run()
    finally:
        questdb_writer.close()


if __name__ == "__main__":
    main()
