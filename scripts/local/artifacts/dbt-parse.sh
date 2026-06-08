#!/usr/bin/env bash
set -euo pipefail

DBT_PROJECT_DIR="${DBT_PROJECT_DIR:-domains/sales/transformations/dbt}"

require_cmd() {
  local cmd="$1"
  if ! command -v "${cmd}" &>/dev/null; then
    printf "ERROR: '%s' not found on PATH\n" "${cmd}" >&2
    exit 1
  fi
}

require_cmd dbt

export AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-openlakeforge}"
export AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-openlakeforge}"
export AWS_REGION="${AWS_REGION:-us-east-1}"
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-${AWS_REGION}}"
export AWS_ENDPOINT_URL_S3="${AWS_ENDPOINT_URL_S3:-http://seaweedfs-s3:8333}"
export OPENLAKEFORGE_DUCKDB_S3_ENDPOINT="${OPENLAKEFORGE_DUCKDB_S3_ENDPOINT:-seaweedfs-s3:8333}"
export POLARIS_DBT_CLIENT_ID="${POLARIS_DBT_CLIENT_ID:-openlakeforge-dbt}"
export POLARIS_DBT_CLIENT_SECRET="${POLARIS_DBT_CLIENT_SECRET:-openlakeforge-dbt}"
export POLARIS_REST_URI="${POLARIS_REST_URI:-http://polaris:8181/api/catalog}"
export POLARIS_TOKEN_URI="${POLARIS_TOKEN_URI:-http://polaris:8181/api/catalog/v1/oauth/tokens}"
export POLARIS_WAREHOUSE="${POLARIS_WAREHOUSE:-lakehouse}"
export POLARIS_OAUTH_SCOPE="${POLARIS_OAUTH_SCOPE:-PRINCIPAL_ROLE:ALL}"

dbt parse \
  --project-dir "${DBT_PROJECT_DIR}" \
  --profiles-dir "${DBT_PROJECT_DIR}" \
  --target local
