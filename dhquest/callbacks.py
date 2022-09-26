import asyncio
from aiokafka import AIOKafkaProducer
from cryptofeed.backends.backend import BackendCallback
from cryptofeed.backends.quest import QuestCallback
from yapic import json


class DHTradeQuest(QuestCallback, BackendCallback):
    default_key = 'trades'

    async def write(self, data):
        timestamp = data["timestamp"]
        received_timestamp_int = int(data["receipt_timestamp"] * 1_000_000)
        timestamp_int = int(timestamp * 1_000_000_000) if timestamp is not None else received_timestamp_int * 1000
        update = f'{self.key},symbol={data["symbol"]},side={data["side"]},type={data["type"]} ' \
                 f'price={data["price"]},size={data["amount"]},id={data["id"]}i,receipt_timestamp={received_timestamp_int}t {timestamp_int}'
        await self.queue.put(update)


class DHBookQuest(QuestCallback):
    default_key = 'book'

    def __init__(self, *args, depth=5, **kwargs):
        super().__init__(*args, **kwargs)
        self.depth = depth

    async def __call__(self, book, receipt_timestamp: float):
        vals = ','.join([f"bid_{i}_price={book.book.bids.index(i)[0]},bid_{i}_size={book.book.bids.index(i)[1]}" for i in range(self.depth)] + [f"ask_{i}_price={book.book.asks.index(i)[0]},ask_{i}_size={book.book.asks.index(i)[1]}" for i in range(self.depth)])
        timestamp = book.timestamp
        receipt_timestamp_int = int(receipt_timestamp * 1_000_000)
        timestamp_int = int(timestamp * 1_000_000_000) if timestamp is not None else receipt_timestamp_int * 1000
        update = f'{self.key}_{self.depth},symbol={book.symbol} {vals},receipt_timestamp={receipt_timestamp_int}t {timestamp_int}'
        await self.queue.put(update)


class KafkaCallback:
    def __init__(self, bootstrap='127.0.0.1', port=9092, topic=None, numeric_type=float, none_to=None, **kwargs):
        """
        bootstrap: str, list
            if a list, should be a list of strings in the format: ip/host:port, i.e.
                192.1.1.1:9092
                192.1.1.2:9092
                etc
            if a string, should be ip/port only
        """
        self.bootstrap = bootstrap
        self.port = port
        self.producer = None
        self.topic = topic if topic else self.default_topic
        self.numeric_type = numeric_type
        self.none_to = none_to

    async def __connect(self):
        if not self.producer:
            loop = asyncio.get_event_loop()
            self.producer = AIOKafkaProducer(acks=0,
                                             loop=loop,
                                             bootstrap_servers=f'{self.bootstrap}:{self.port}' if isinstance(
                                                 self.bootstrap, str) else self.bootstrap,
                                             client_id='cryptofeed')
            await self.producer.start()

    async def write(self, data: dict):
        await self._connect()
        await self.producer.send_and_wait(self.topic, json.dumps(data).encode('utf-8'))



class DHTradeKafka(KafkaCallback):
    default_topic = 'trades'

    async def __call__(self, dtype, receipt_timestamp: float):
        if isinstance(dtype, dict):
            data = dtype
        else:
            data = dtype.to_dict(numeric_type=self.numeric_type, none_to=self.none_to)
            if not dtype.timestamp:
                data['timestamp'] = receipt_timestamp
            data['receipt_timestamp'] = receipt_timestamp
        await self.write(data)

    async def write(self, data: dict):
        await self._KafkaCallback__connect()
        try:
            # FIXME: normalize CEX/DEX fields so we don't need this hack
            data['ts'] = int(data.pop('timestamp') * 1_000_000_000)
            data['receipt_ts'] = int(data.pop('receipt_timestamp') * 1000)
            data['size'] = data.pop('amount')
            data['trade_id'] = data.pop('id')
        except:
            pass
        await self.producer.send_and_wait(self.topic, json.dumps(data).encode('utf-8'))

