FROM ghcr.io/deephaven/server-ui:0.40.7
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH="${PYTHONPATH}:/data/storage/notebooks"

RUN pip install --no-cache-dir questdb polars pyarrow connectorx websockets orjson seaborn psycopg2-binary sqlalchemy adbc-driver-postgresql sqlalchemy

COPY ./config/deephaven.prop /opt/deephaven/config/deephaven.prop

# Apply TableDataService patch for custom backends
COPY ./patch_all_callbacks.py /tmp/patch_all_callbacks.py
RUN python3 /tmp/patch_all_callbacks.py && rm /tmp/patch_all_callbacks.py
