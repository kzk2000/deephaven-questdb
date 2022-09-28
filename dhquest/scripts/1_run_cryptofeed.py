import os
import time
from cryptofeed import FeedHandler
from cryptofeed.defines import TRADES
from cryptofeed.exchanges import Coinbase

from dhquest.dhcallbacks import DHTradeQuest, DHTradeKafka


async def my_print(data, _receipt_time):
    print(data)


def main():
    # see docker_files/Dockerfile.cryptofeed where we set IS_DOCKER=True
    # by doing this here, we can also run this script locally
    # see https://www.confluent.io/blog/kafka-client-cannot-connect-to-broker-on-aws-on-docker-etc/#scenario-4
    kakfa_bootstrap = 'redpanda' if os.environ.get('IS_DOCKER') else 'localhost'
    kakfa_port = 29092 if os.environ.get('IS_DOCKER') else 9092
    dh_tradekafka = DHTradeKafka(bootstrap=kakfa_bootstrap, port=kakfa_port)

    dh_tradequest = DHTradeQuest(host='192.168.0.10', port=9009)

    f = FeedHandler()
    f.add_feed(Coinbase(channels=[TRADES],
                        symbols=['BTC-USD', 'ETH-USD', 'SOL-USD', 'AVAX-USD'],
                        callbacks={TRADES: [dh_tradekafka, dh_tradequest, my_print]}))
    f.run()


if __name__ == '__main__':
    if os.environ.get('IS_DOCKER'):
        print('Delay start by 10sec so that Kafka broker and QuestDB are ready')
        time.sleep(10)

    main()
