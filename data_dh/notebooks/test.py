

candles = db.get_candles('5s')





with get_connection() as conn:
    trades = dhpd.to_table(pd.read_sql_query(query_trades, conn))



query_orderbook = """
SELECT * FROM book_5
LIMIT -10
"""

with get_connection() as conn:
    quotes = dhpd.to_table(pd.read_sql_query(query_orderbook, conn))



query_L1 = """
SELECT 
  symbol
  , bid_0_size  AS bid_size
  , bid_0_price AS bid
  , ask_0_price AS ask
  , ask_0_size  AS ask_size 
  , (bid_0_price + ask_0_price) / 2 AS mid
FROM book_5
--LIMIT -10
"""

with get_connection() as conn:
    quotes = dhpd.to_table(pd.read_sql_query(query_L1, conn))


#{"exchange":"COINBASE","side":"sell","price":1305.83,"type":null,"receipt_timestamp":1664162012.6063333,"ts":1664162012.594655,"size":0.05360001,"product_id":"ETH-USD","trade_id":"360885562"}
trades_stream = ck.consume(
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
 ])
    

