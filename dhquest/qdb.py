import pandas as pd
import psycopg2
from deephaven import dtypes, new_table
from deephaven import numpy as dhnp
from deephaven import pandas as dhpd  # this assumes you are running from DH UI site, otherwise this will throw


def get_connection():
    return psycopg2.connect(
        user='admin',
        password='quest',
        host='192.168.0.10',
        port='8812',
        database='qdb')


def to_table(df):
    """
    This is just a variant of deephaven.pandas.to_table() to always cast the 'object' from
    input df as Java string.
    """
    cols = list(df)

    input_cols = []
    for col in cols:
        np_array = df.get(col).values
        dtype = dtypes.from_np_dtype(np_array.dtype)
        if dtype is dtypes.PyObject:
            dtype = dtypes.string

        np_array = dhpd._map_na(np_array)
        input_cols.append(dhnp._make_input_column(col, np_array, dtype))

    return new_table(cols=input_cols)


def run_query(query):
    with get_connection() as conn:
        return to_table(pd.read_sql_query(query, conn))


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
