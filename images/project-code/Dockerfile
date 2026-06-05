FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DAGSTER_HOME=/opt/dagster/dagster_home

WORKDIR /opt/openlakeforge

COPY images/project-code/pyproject.toml ./pyproject.toml

RUN pip install --no-cache-dir --upgrade pip \
    && python -c "import subprocess, sys, tomllib; from pathlib import Path; dependencies = tomllib.load(Path('pyproject.toml').open('rb'))['project']['dependencies']; subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--no-cache-dir', *dependencies])"

COPY domains ./domains
COPY libs ./libs

RUN pip install --no-cache-dir --no-deps .

RUN mkdir -p "${DAGSTER_HOME}"

CMD ["dagster", "api", "grpc", "--module-name", "domains.sales.pipelines.dagster.definitions", "--host", "0.0.0.0", "--port", "3030"]
