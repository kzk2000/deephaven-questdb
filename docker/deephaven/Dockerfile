FROM ghcr.io/deephaven/server-ui:41.0
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH="${PYTHONPATH}:/data/storage/notebooks"

RUN pip install --no-cache-dir questdb polars pyarrow connectorx websockets orjson seaborn psycopg2-binary sqlalchemy adbc-driver-postgresql sqlalchemy

COPY ./config/deephaven.prop /opt/deephaven/config/deephaven.prop
