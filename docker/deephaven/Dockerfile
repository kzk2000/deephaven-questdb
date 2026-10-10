FROM ghcr.io/deephaven/server-ui:42.6
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH="${PYTHONPATH}:/data/storage/notebooks"

# Only what the notebooks use; exact versions are in constraints.txt so a rebuild cannot
# change behaviour (SQLAlchemy 2.1 silently changed the default postgresql:// driver once).
#   questdb       QWP reads (qdb_qwp.py)
#   connectorx    optional Postgres-wire fallback for static snapshots (qdb.py transport="pg")
#   SQLAlchemy +
#   psycopg2      live-backend queries via get_engine() (postgresql+psycopg2://)
#   tradingview   TradingView charts (trades_chart_live.py)
# pyarrow, pandas, numpy and matplotlib come with the Deephaven base image.
COPY constraints.txt /tmp/constraints.txt
RUN pip install --no-cache-dir -c /tmp/constraints.txt \
    questdb \
    connectorx \
    SQLAlchemy \
    psycopg2-binary \
    deephaven-plugin-tradingview-lightweight

COPY ./config/deephaven.prop /opt/deephaven/config/deephaven.prop
