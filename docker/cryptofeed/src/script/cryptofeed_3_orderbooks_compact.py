import asyncio
import os
from cryptofeed import FeedHandler
from cryptofeed.defines import L2_BOOK
from cryptofeed.exchanges import Coinbase, Kraken, Bitstamp

import src.cryptofeed_tools as cft
from src.questdb_writer import QuestDBWriter


# Initialize QuestDB writer (global for all callbacks)
questdb_host = 'questdb' if os.environ.get('IS_DOCKER') else '127.0.0.1'
questdb_writer = QuestDBWriter(host=questdb_host, port=9009)

# Orderbook depth configuration
# Limit to 20 levels to avoid huge data from exchanges like Coinbase
ORDERBOOK_DEPTH = 20  # Set to integer to limit, None for full depth


async def write_to_questdb_compact(book, receipt_timestamp):
    """
    Callback to write orderbook snapshots to QuestDB in compact JSON format
    """
    try:
        questdb_writer.write_orderbook_compact(book, receipt_timestamp, depth=ORDERBOOK_DEPTH)
    except Exception as e:
        print(f"Error in compact orderbook callback: {e}")


def main():
    callbacks = {L2_BOOK: [write_to_questdb_compact, cft.my_print]}
    
    # Use BTC-USD only for testing compact format
    test_symbols = ['BTC-USD']
    
    f = FeedHandler()
    # Using snapshots_only=True and snapshot_interval to control data volume
    # For testing, using lower interval to see data quickly
    f.add_feed(Coinbase(max_depth=2000, channels=[L2_BOOK], symbols=test_symbols, 
                       callbacks=callbacks, snapshot_interval=100, snapshots_only=True))
    f.add_feed(Bitstamp(channels=[L2_BOOK], symbols=test_symbols, 
                       callbacks=callbacks, snapshot_interval=100, snapshots_only=True))
    f.add_feed(Kraken(channels=[L2_BOOK], symbols=test_symbols, 
                     callbacks=callbacks, snapshot_interval=100, snapshots_only=True))
    
    # Fix for Python 3.10+ asyncio event loop issue
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    f.run()


if __name__ == '__main__':
    main()
