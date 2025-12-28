import deephaven.arrow as dhpa
import connectorx as cx
import pyarrow as pa
import pyarrow.compute as pc


def to_table(arrow_table):
    """
    Custom conversion from Arrow table to Deephaven table.
    Adds timezone to timestamp[us] and converts large_list to list.

    The key is using timezone-aware timestamps: timestamp[us, tz=UTC]
    This matches what qdb_backend.py does for live tables.
    """
    schema = arrow_table.schema
    new_columns = []
    new_fields = []

    for i, field in enumerate(schema):
        col = arrow_table.column(i)

        if pa.types.is_timestamp(field.type):
            # Convert timestamp[us] to timestamp[ns, tz=UTC]
            # Deephaven requires nanosecond precision with timezone
            if field.type.unit == "us":
                # Convert us to ns (multiply by 1000) and add UTC timezone
                new_field = pa.field(field.name, pa.timestamp("ns", tz="UTC"))
                new_col = pc.cast(col, new_field.type)
                new_columns.append(new_col)
                new_fields.append(new_field)
            else:
                new_columns.append(col)
                new_fields.append(field)
        elif pa.types.is_large_list(field.type):
            # Convert large_list to regular list for Deephaven compatibility
            value_type = field.type.value_type
            new_field = pa.field(field.name, pa.list_(value_type))
            new_col = pc.cast(col, new_field.type)
            new_columns.append(new_col)
            new_fields.append(new_field)
        else:
            new_columns.append(col)
            new_fields.append(field)

    # Create new Arrow table with converted types
    converted_table = pa.table(new_columns, schema=pa.schema(new_fields))

    return dhpa.to_table(converted_table)


# Connection strings for QuestDB
# ConnectorX uses 'redshift://' protocol, psycopg2/SQLAlchemy uses 'postgresql://'
_CONNECTORX_STRING = "redshift://admin:quest@questdb:8812/qdb"
_POSTGRES_STRING = "postgresql://admin:quest@questdb:8812/qdb"

# Lazy-initialized SQLAlchemy engine for qdb_backend.py
_engine = None


def get_connection():
    """
    Returns the connection string for ConnectorX.
    Used by static notebooks (trades_static.py, orderbooks_static.py).
    """
    return _CONNECTORX_STRING


def get_engine():
    """
    Returns a SQLAlchemy engine for qdb_backend.py.
    Used by live notebooks (trades_live_via_questdb_wal.py, etc).

    Creates engine lazily on first call.
    """
    global _engine
    if _engine is None:
        from sqlalchemy import create_engine

        _engine = create_engine(_POSTGRES_STRING, pool_pre_ping=True)
    return _engine


def run_query(query):
    """
    Execute a SQL query and return a Deephaven table.
    Uses connectorx to load to Arrow (most efficient),
    then uses custom to_table() for proper timestamp conversion.

    Args:
        query: SQL query string

    Returns:
        Deephaven table
    """
    conn_str = get_connection()
    # ConnectorX loads directly to Arrow (most efficient format)
    arrow_table = cx.read_sql(conn_str, query, return_type="arrow")
    # Use custom to_table() that handles timestamp precision conversion
    return to_table(arrow_table)


def get_trades(last_nticks=1000, verbose=False):
    query = f"""
    SELECT
        timestamp,
        exchange,
        symbol,
        price,
        size,
        side
    FROM trades
    LIMIT -{abs(last_nticks)}
    """
    if verbose:
        print(query)

    return run_query(query)


def get_candles(sample_by="5s", verbose=False):
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


def get_orderbooks(last_n=1000, verbose=False):
    """
    Get orderbook snapshots from QuestDB.
    Uses orderbooks_1s view (1-second sampled data).

    NOTE: This returns only metadata (timestamp, exchange, symbol).
    For full orderbook data with bids/asks arrays, use get_orderbooks_2d().

    Args:
        last_n: Number of most recent snapshots to fetch
        verbose: Print the query

    Returns:
        Deephaven table with orderbook metadata
    """
    query = f"""
    SELECT
    timestamp
    , exchange
    , symbol
    , bids[1] as bid_prices
    , bids[2] AS bid_sizes
    , asks[1] AS ask_prices
    , asks[2] AS ask_sizes
    FROM orderbooks_1s
    LIMIT -{abs(10)}
    """
    if verbose:
        print(query)

    return run_query(query)
