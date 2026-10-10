FROM ghcr.io/deephaven/server-ui:42.6
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH="${PYTHONPATH}:/data/storage/notebooks"

RUN pip install --no-cache-dir questdb polars pyarrow connectorx websockets orjson seaborn psycopg2-binary sqlalchemy adbc-driver-postgresql \
    deephaven-plugin-tradingview-lightweight==0.0.1

COPY ./config/deephaven.prop /opt/deephaven/config/deephaven.prop
