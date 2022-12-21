# Deephaven + QuestDB persistent storage
### TLDR;
Deephaven Community doesn't provide built-in connectivity to a DB backend (as of Oct 2022). Here, we leverage QuestDB to add that persistence layer, by   
* subscribing to [Cryptofeed](https://github.com/bmoscon/cryptofeed)'s websockets
* pushing the tick data onto Kafka to create data streams for Deephaven
* persisting all the data to QuestDB to collect historical data
* accessing streams and historical data from the DH UI 

### One-time setup (only required to do once per host!)
* Create a docker network with dedicated IP range so all docker containers can talk to each other<br>
```docker network create --subnet "192.168.0.0/16" dhquestnet```

### Step 1: Start Kafka broker, QuestDB server, and the Cryptofeed data producer
* To see what data we subscribe to, see [1_run_cryptofeed.py](./dhquest/scripts/1_run_cryptofeed.py)
* QuestDB server is running at http://192.168.0.10:9000/, you should see a 'trades' table right away
* Ideally, these 3 containers just run forever.
```
docker-compose -f docker-compose-base.yml build --force
docker-compose -f docker-compose-base.yml up -d
```
To make sure data is ticking, run ```docker logs cryptofeed -n10 -f -t``` and you should see ticks coming in after a 10sec delay.<br>
Worst case, try ```docker stop cryptofeed && docker start crytpofeed```. 
### Step 2: Start Deephaven servers 
```
docker-compose -f docker-compose-deephaven.yml build --force  
docker-compose -f docker-compose-deephaven.yml up -d
```
### Step 3: Open Deephaven Web UI
Go to http://localhost:10000/ide/) and open the [dh_questdb.py](data/notebooks/dh_questdb.py) from the File Explorer. <br>
Code shown here for quick reference:
```python
import deephaven.dtypes as dht
from deephaven.stream.kafka.consumer import TableType, KeyValueSpec
from deephaven import kafka_consumer as ck
from deephaven import pandas as dhpd
from dhquest import qdb  # custom lib


########################################
# call wrapper func to QuestDB
trades = qdb.get_trades(last_nticks=1000)

candles = qdb.get_candles(sample_by='1m')


########################################
# call QuestDB SQL directly 
query = """
    SELECT * FROM trades
    WHERE symbol = 'BTC-USD'
    LIMIT -200
"""    
trades_btc = qdb.run_query(query)

candles_btc = candles.where(['symbol==`BTC-USD`'])


########################################
# or subscribe to stream from  Kafka
trades_latest = ck.consume(
    {'bootstrap.servers': 'redpanda:29092'},
    'trades',
    key_spec=KeyValueSpec.IGNORE,
    value_spec = ck.json_spec([
        ('ts', dht.DateTime),
        ('receipt_ts', dht.DateTime),
        ('symbol', dht.string),
        ('exchange', dht.string),
        ('side', dht.string),
        ('size', dht.double),
        ('price', dht.double),
    ]),    
    table_type=TableType.stream())\
.update_view([
   "latency_ms = (receipt_ts - ts) / 1e6",
 ]).last_by(['symbol'])
```    



