
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
    LIMIT -20000
"""    
trades_btc = qdb.run_query(query)

candles_btc = candles.where(['(String)symbol==`BTC-USD`'])


candles2 = candles.select(["symbol2 = (java.lang.String)symbol")
meta_table = candles.meta_table

from deephaven import new_table

from deephaven.column import string_col, int_col

result = new_table([
   string_col("Name_Of_String_Col", candles.select(['symbol_[ii]'])),   
])


kk = candles.select(['X=symbol_[ii]'])
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
    
