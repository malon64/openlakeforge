ARG SUPERSET_BASE_IMAGE=apache/superset:6.1.0
FROM ${SUPERSET_BASE_IMAGE}

USER root

RUN uv pip install --python /app/.venv/bin/python --no-cache \
    "psycopg2-binary>=2.9,<3" \
    "trino>=0.336,<1"

COPY patches/patch_trino_iceberg_partitions.py /tmp/openlakeforge/patch_trino_iceberg_partitions.py
RUN /app/.venv/bin/python /tmp/openlakeforge/patch_trino_iceberg_partitions.py \
    && rm /tmp/openlakeforge/patch_trino_iceberg_partitions.py

USER superset
