# Deephaven with QuestDB persistent store
Deephaven Community doesn't currently provide a solution to persist data on a server backend. Here we show how we can leverage QuestDB to do it anyway, by
* subscribing to [Cryptofeed](https://github.com/bmoscon/cryptofeed)'s websockets
* pushing the data onto Kafka to create a stream
* persisting the data in QuestDB
* modifying the Deephaven Community version to access QuestDB via Postgres db connector 

### One-time setup (only do this once a given host!)
* Create a docker network with dedicated IP range so all docker containers can talk to each other<br>
```docker network create --subnet "192.168.0.0/16" dhquestnet```

### Start all servers
* Start the Redpanda's Kafka broker and the QuestDB server (ideally, these will run forever!). We run this as separate docker-compose so we can restart the Deephaven server as needed while we keep streaming data into QuestDB:<br>
```docker-compose -f docker-compose-base.yml up -d```

* Start the Deephaven server and UI:<br>
```docker-compose -f docker-compose-deephaven.yml up -d```


## Create a conda env (or whatever you prefer) and start producing some tick data via Cryptofeed  
```     
    conda create -n dh_quest python=3.7
    conda activate dh_questdb
    pip install -r requirements.txt
    python dhquest/1_run_cryptofeed.py      
```



## Deephaven IDE 
Head over to http://localhost:10000/ide/ (if it's not working, try re-running [3_start_deephaven.sh](./3_start_deephaven.sh) (see above), 
and copy paste this to the Deephaven IDE and run it with Ctrl+Alt+R:
```python
from deephaven.ParquetTools import writeTable, readTable
from deephaven import ConsumeKafka as ck
from deephaven import Types as dht
from deephaven.MovingAverages import ByEmaSimple
from deephaven import Aggregation as agg, as_list
from deephaven import tableToDataFrame, dataFrameToTable
from deephaven.DateTimeUtils import convertDateTime
import pandas as pd
from deephaven.DateTimeUtils import autoEpochToTime
import datetime
from deephaven.TableTools import timeTable
#{"exchange":"COINBASE","symbol":"BTC-USD","side":"sell","amount":0.00133965,"price":44963.12,"id":"304048514","type":null,"timestamp":1648404388.130469,"receipt_timestamp":1648404388.155557}

def parse_timestamp(x):
    return convertDateTime(datetime.datetime.utcfromtimestamp(x).isoformat() + ' UTC')


trades_dex = ck.consumeToTable(
    {'bootstrap.servers': 'redpanda:29092'},
    'trades_dex',
    key = ck.IGNORE,   
    value = ck.json([
            ('ts', dht.double),            
            ('exchange', dht.string),
            ('product_id', dht.string),
            ('side', dht.string),
            ('size', dht.double),
            ('price', dht.double),
            ('trade_id', dht.string),
            ]),   
    offsets = {0: 0},
    table_type='append')\
    .updateView("ts = (DateTime)parse_timestamp(ts)")


trades_cex = ck.consumeToTable(
    {'bootstrap.servers': 'redpanda:29092'},
    'trades',
    key = ck.IGNORE,   
    value = ck.json([
            ('ts', dht.double),
            ('exchange', dht.string),
            ('product_id', dht.string),
            ('side', dht.string),
            ('size', dht.double),
            ('price', dht.double),
            ('trade_id', dht.string),
            ]),
    offsets = {0: 0},
    table_type='append')\
    .updateView("ts = (DateTime)parse_timestamp(ts)")
    
```






