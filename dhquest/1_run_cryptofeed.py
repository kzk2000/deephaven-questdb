from cryptofeed import FeedHandler
from cryptofeed.defines import TRADES
from cryptofeed.exchanges import Coinbase

from dhquest.callbacks import DHTradeQuest, DHTradeKafka

QUEST_HOST = '192.168.0.10'
QUEST_PORT = 9009


def main():
    f = FeedHandler()
    f.add_feed(Coinbase(channels=[TRADES],
                        symbols=['BTC-USD', 'ETH-USD', 'SOL-USD', 'AVAX-USD'],
                        callbacks={TRADES: [DHTradeQuest(host=QUEST_HOST, port=QUEST_PORT), DHTradeKafka()]}))
    f.run()


if __name__ == '__main__':
    main()