import deephaven.dtypes as dht
from deephaven import kafka_consumer as ck
from deephaven import pandas as dhpd  # this assumes you are running from DH UI site, otherwise this will throw
import pandas as pd
import psycopg2


def get_connection():
    return psycopg2.connect(
        user='admin',
        password='quest',
        host='192.168.0.10',
        port='8812',
        database='qdb')


def run_query(query):
    with get_connection() as conn:
        return dhpd.to_table(pd.read_sql_query(query, conn))


def get_trades(last_nticks=1000, verbose=False):
    query = f"""
    SELECT * FROM trades
    LIMIT -{abs(last_nticks)}
    """
    if verbose:
        print(query)

    return run_query(query)


def get_candles(sample_by='5s', verbose=False):
    query = f"""
    SELECT   
      timestamp                         AS ts
      , symbol
      , first(price)                    AS openp
      , min(price)                      AS lowp
      , max(price)                      AS highp
      , last(price)                     AS closep
      , sum(price * size) / sum(size)   AS vwap
      , sum(size)                       AS volume_base
      , sum(price * size)               AS volume_quote
    FROM trades
    SAMPLE BY {sample_by}
    """
    if verbose:
        print(query)

    return run_query(query)

