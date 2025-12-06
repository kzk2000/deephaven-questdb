# Using edge build for TableDataService API (added Nov 1, 2024)
# TODO: Switch to stable v0.41.0 when released (expected Q1 2025)
FROM ghcr.io/deephaven/server:edge
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH="${PYTHONPATH}:/data/storage/notebooks"

RUN pip install --no-cache-dir questdb websockets orjson seaborn psycopg2-binary adbc-driver-postgresql pyarrow

COPY ./config/deephaven.prop /opt/deephaven/config/deephaven.prop
