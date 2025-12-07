FROM ghcr.io/deephaven/server:0.40.7
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH="${PYTHONPATH}:/data/storage/notebooks"

RUN pip install --no-cache-dir questdb websockets orjson seaborn psycopg2-binary adbc-driver-postgresql pyarrow

COPY ./config/deephaven.prop /opt/deephaven/config/deephaven.prop

# Apply TableDataService patch for custom backends
COPY ./patch_all_callbacks.py /tmp/patch_all_callbacks.py
RUN python3 /tmp/patch_all_callbacks.py && rm /tmp/patch_all_callbacks.py
