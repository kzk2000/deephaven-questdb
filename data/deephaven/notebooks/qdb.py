import threading

import deephaven.arrow as dhpa
import pyarrow as pa
import pyarrow.compute as pc

# How run_query() reads QuestDB: "qwp" (Arrow over QuestDB's WebSocket protocol, the default;
# see qdb_qwp.py) or "pg" (ConnectorX over the Postgres wire, the previous path). Override per
# call (run_query(sql, transport="pg")) or globally by setting qdb.DEFAULT_TRANSPORT.
DEFAULT_TRANSPORT = "qwp"


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
_POSTGRES_STRING = "postgresql+psycopg2://admin:quest@questdb:8812/qdb"

# Lazy-initialized SQLAlchemy engine for qdb_backend.py
_engine = None


def get_connection():
    """
    Returns the connection string for ConnectorX (the transport="pg" path of run_query).
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


_reader = None
_reader_lock = threading.Lock()


def _qwp_reader():
    """Shared QWP connection (lazy; QwpReader serializes queries and reconnects once)."""
    global _reader
    with _reader_lock:
        if _reader is None:
            import qdb_qwp

            _reader = qdb_qwp.QwpReader()
        return _reader


def _query_arrow(query, transport):
    if transport == "qwp":
        # nanosecond timestamps, SYMBOLs decoded to strings, nested arrays typed double[][]
        import qdb_qwp

        table = _qwp_reader().page_sql(query)
        hinted = qdb_qwp.with_deephaven_hints(table.schema)
        return table.cast(hinted) if hinted != table.schema else table
    if transport == "pg":
        import connectorx as cx  # only needed for the Postgres-wire path

        return cx.read_sql(get_connection(), query, return_type="arrow")
    raise ValueError(f"transport must be 'qwp' or 'pg', got {transport!r}")


def run_query(query, transport=None):
    """
    Execute a SQL query and return a Deephaven table.

    Reads Arrow from QuestDB over QWP by default (transport="pg" uses ConnectorX over the
    Postgres wire), then uses custom to_table() for proper timestamp conversion.

    Args:
        query: SQL query string
        transport: "qwp" or "pg"; defaults to qdb.DEFAULT_TRANSPORT

    Returns:
        Deephaven table
    """
    arrow_table = _query_arrow(query, transport or DEFAULT_TRANSPORT)
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
    Uses orderbooks_1s view (1-second sampled data, kept after the raw
    orderbooks table expires via its 1 hour TTL).

    Args:
        last_n: Number of most recent snapshots to fetch
        verbose: Print the query

    Returns:
        Deephaven table with timestamp, exchange, symbol and the bid/ask
        price and size arrays (bid_prices, bid_sizes, ask_prices, ask_sizes)
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
    LIMIT -{abs(int(last_n))}
    """
    if verbose:
        print(query)

    return run_query(query)
