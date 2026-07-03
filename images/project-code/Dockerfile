ARG PYTHON_BASE_IMAGE=python:3.12-slim
FROM ${PYTHON_BASE_IMAGE}
ARG DBT_PROFILE_ENV=local

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

# Render environment-specific dbt profiles, then pre-generate dbt manifests with
# the lightweight local target so code-server pods do not parse on every rollout.
RUN set -e; \
    python -m libs.dbt.render_profiles --environment "${DBT_PROFILE_ENV}" --write; \
    for project_dir in $(find domains -path "*/transformations/dbt/*/dbt_project.yml" -type f -exec dirname {} \; | sort); do \
      dbt deps --project-dir "${project_dir}"; \
      dbt parse --project-dir "${project_dir}" --profiles-dir "${project_dir}" --target local; \
      echo "dbt manifest baked at ${project_dir}/target/manifest.json"; \
    done

RUN mkdir -p "${DAGSTER_HOME}"

CMD ["dagster", "api", "grpc", "--module-name", "domains.sales.definitions", "--host", "0.0.0.0", "--port", "3030"]
