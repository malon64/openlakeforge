FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DAGSTER_HOME=/opt/dagster/dagster_home

WORKDIR /opt/openlakeforge

COPY pyproject.toml ./
COPY domains ./domains

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

RUN mkdir -p "${DAGSTER_HOME}"

CMD ["dagster", "api", "grpc", "--module-name", "domains.sales.orchestration.dagster.definitions", "--host", "0.0.0.0", "--port", "3030"]
