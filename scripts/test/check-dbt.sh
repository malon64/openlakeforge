#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="domains/sales/transformations/dbt"

require_cmd() {
  local cmd="$1"
  if ! command -v "${cmd}" &>/dev/null; then
    printf "ERROR: '%s' not found on PATH\n" "${cmd}" >&2
    exit 1
  fi
}

require_cmd python3

CACHE_ROOT="${DBT_CHECK_CACHE_DIR:-.cache/dbt-check}"
python_tag="$(python3 - <<'PY'
import sys
print(f"py{sys.version_info.major}{sys.version_info.minor}")
PY
)"
dependency_hash="$(python3 - <<'PY'
import hashlib
from pathlib import Path
payload = "\n".join([
    "dbt-duckdb>=1.9.6,<1.10",
    "duckdb>=1.4.1,<1.5",
])
print(hashlib.sha256(payload.encode()).hexdigest()[:16])
PY
)"
site_dir="${CACHE_ROOT}/${python_tag}-${dependency_hash}/site"
stamp_path="${CACHE_ROOT}/${python_tag}-${dependency_hash}/.complete"

if [[ ! -f "${stamp_path}" ]]; then
  rm -rf "${CACHE_ROOT:?}/${python_tag}-${dependency_hash}"
  mkdir -p "${site_dir}"

  echo "==> Installing dbt-duckdb check dependencies into ${site_dir}"
  PYTHONDONTWRITEBYTECODE=1 python3 -m pip install \
    --disable-pip-version-check \
    --no-compile \
    --prefer-binary \
    --target "${site_dir}" \
    "dbt-duckdb>=1.9.6,<1.10" \
    "duckdb>=1.4.1,<1.5"
  touch "${stamp_path}"
else
  echo "==> Reusing dbt dependency cache ${site_dir}"
fi

export PYTHONPATH="${site_dir}${PYTHONPATH:+:${PYTHONPATH}}"
export PATH="${site_dir}/bin:${PATH}"

export AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-openlakeforge}"
export AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-openlakeforge}"
export AWS_REGION="${AWS_REGION:-us-east-1}"
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-${AWS_REGION}}"
export AWS_ENDPOINT_URL_S3="${AWS_ENDPOINT_URL_S3:-http://seaweedfs-s3:8333}"
export POLARIS_DBT_CLIENT_ID="${POLARIS_DBT_CLIENT_ID:-openlakeforge-dbt}"
export POLARIS_DBT_CLIENT_SECRET="${POLARIS_DBT_CLIENT_SECRET:-openlakeforge-dbt}"
export POLARIS_REST_URI="${POLARIS_REST_URI:-http://polaris:8181/api/catalog}"
export POLARIS_TOKEN_URI="${POLARIS_TOKEN_URI:-http://polaris:8181/api/catalog/v1/oauth/tokens}"
export POLARIS_WAREHOUSE="${POLARIS_WAREHOUSE:-lakehouse}"
export POLARIS_OAUTH_SCOPE="${POLARIS_OAUTH_SCOPE:-PRINCIPAL_ROLE:ALL}"

echo "==> dbt parse"
dbt parse --project-dir "${PROJECT_DIR}" --profiles-dir "${PROJECT_DIR}"

echo "==> dbt compile"
dbt compile --project-dir "${PROJECT_DIR}" --profiles-dir "${PROJECT_DIR}"
