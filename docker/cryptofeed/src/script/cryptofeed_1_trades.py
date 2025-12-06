import os
import asyncio
from cryptofeed import FeedHandler
from cryptofeed.defines import TRADES
from cryptofeed.exchanges import Coinbase, Bitstamp, Kraken

import src.cryptofeed_tools as cft
from src.questdb_writer import QuestDBWriter


# Initialize QuestDB writer
questdb_host = 'questdb' if os.environ.get('IS_DOCKER') else '127.0.0.1'
questdb_writer = QuestDBWriter(host=questdb_host, port=9009)


async def write_to_questdb(data, receipt_timestamp):
    """Callback to write trade data to QuestDB"""
    # Convert trade object to dict if needed
    if hasattr(data, 'to_dict'):
        trade_data = data.to_dict(numeric_type=float, none_to=None)
    else:
        trade_data = data
    
    # Add receipt timestamp if not present
    if 'receipt_timestamp' not in trade_data:
        trade_data['receipt_timestamp'] = receipt_timestamp
    if not trade_data.get('timestamp'):
        trade_data['timestamp'] = receipt_timestamp
    
    # Write to QuestDB
    questdb_writer.write_trade(trade_data)
    
    # Flush every 100 writes for better throughput
    if not hasattr(write_to_questdb, 'counter'):
        write_to_questdb.counter = 0
    write_to_questdb.counter += 1
    if write_to_questdb.counter >= 100:
        questdb_writer.flush()
        write_to_questdb.counter = 0


def main():
    # cft.SYMBOLS = ['BTC-USD']   # for testing
    
    f = FeedHandler()
    # Write directly to QuestDB using custom writer
    f.add_feed(Coinbase(channels=[TRADES], symbols=cft.SYMBOLS, callbacks={TRADES: [write_to_questdb, cft.my_print]}))
    f.add_feed(Bitstamp(channels=[TRADES], symbols=cft.SYMBOLS, callbacks={TRADES: [write_to_questdb, cft.my_print]}))
    f.add_feed(Kraken(channels=[TRADES], symbols=cft.SYMBOLS, callbacks={TRADES: [write_to_questdb, cft.my_print]}))
    
    # Fix for Python 3.10+ asyncio event loop issue
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    try:
        f.run()
    finally:
        questdb_writer.close()


if __name__ == '__main__':
    main()
