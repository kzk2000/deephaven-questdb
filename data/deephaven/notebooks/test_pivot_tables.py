########################################
# or subscribe to stream from  Kafka
trades = ck.consume(
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
   "latency_ms = (receipt_ts - ts) / 1e6",
   "bin = lowerBin(ts, SECOND * 5)",
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

    retval = None
    for index, key in enumerate(keys_list):
        table_now = partitioned_table.get_constituent([key])
        symbol_now = key.lower().replace('-', '_')
        print(index, symbol_now)
        locals()[f"output_{index}"] = table_now.view(['bin', f'{symbol_now} = quote_size'])

        if index == 0:
            retval = locals()[f"output_0"]  # FIXME: this assumes 1st symbol has all 'bin' timestamps!!!
        else:
            retval = retval.natural_join(locals()[f"output_{index}"], on=['bin'])
    
    return retval


table_to_partition = trades.view(['symbol','bin','quote_size = size*price']).sum_by(['bin','symbol'])

partitioned_table, keys_list = get_partitions(table_to_partition, 'symbol')
print(keys_list)

pivot_table = create_pivots(partitioned_table, keys_list)  

