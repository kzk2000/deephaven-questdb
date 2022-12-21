import deephaven.dtypes as dht
from deephaven.stream.kafka.consumer import TableType, KeyValueSpec
from deephaven import kafka_consumer as ck
from deephaven import pandas as dhpd
from deephaven.plot.figure import Figure
from dhquest import qdb  # custom lib


########################################
# call wrapper func to QuestDB
trades = qdb.get_trades(last_nticks=1000)

candles = qdb.get_candles(sample_by='1m')
candles_btc = candles.where(['symbol == `BTC-USD`'])

# plot candles from QuestDB (static, will not update)
plot_btc_candles = Figure()\
    .chart_title(title="BTC OHLC - 1min candles from QuestDB (non-ticking)")\
    .plot_ohlc(series_name="BTC", t=candles_btc, x="ts", open="openp", high="highp", low="lowp", close="closep")\
    .show()

########################################
# call QuestDB SQL directly 
query = """
    SELECT * FROM trades
    WHERE symbol = 'BTC-USD'
    LIMIT -200
"""    
trades_btc = qdb.run_query(query)


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
    table_type=TableType.ring(10000))\
.update_view([
   "bin = upperBin(ts, MINUTE * 1)",
   "latency_ms = (receipt_ts - ts) / 1e6",   
 ]).tail_by(1000,['symbol'])\
 .drop_columns(['KafkaPartition', 'KafkaOffset', 'KafkaTimestamp'])







########################################
# create partitions and pivot them into columns

def get_partitions(org_table, partition_by: str):

    partitioned_table = org_table.partition_by([partition_by])
    keys_table = partitioned_table.table.select_distinct(partitioned_table.key_columns)  # a DH 1 column table of unique keys
    iterator = keys_table.j_object.columnIterator(partition_by)  # this is a Java iterator
    keys_list = []
    while iterator.hasNext():
        keys_list.append(iterator.next())
    return partitioned_table, keys_list


def create_pivots(partitioned_table, keys_list):

    for index, key in enumerate(keys_list):
        table_now = partitioned_table.get_constituent([key])
        symbol_now = key.lower().replace('-', '_')
        print(symbol_now)
        locals()[f"output_{index}"] = table_now.view(['bin', f'{symbol_now} = quote_size'])

        if index == 0:
            locals()[f"output_final"] = locals()[f"output_0"]  # FIXME: this assumes 1st symbol has all 'bin' timestamps!!!
        else:
            locals()[f"output_final"] = locals()[f"output_final"].natural_join(locals()[f"output_{index}"], on=['bin'])
    
    return locals()[f"output_final"]


table_to_partition = trades_latest.view(['symbol','bin','quote_size = size*price']).sum_by(['bin','symbol'])

partitioned_table, keys_list = get_partitions(table_to_partition, 'symbol')
print(keys_list)

pivot_table = create_pivots(partitioned_table, keys_list)  



