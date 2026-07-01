#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

require_cmd() {
  local cmd="$1"
  if ! command -v "${cmd}" &>/dev/null; then
    printf "ERROR: '%s' not found on PATH\n" "${cmd}" >&2
    exit 1
  fi
}

require_cmd dbt
require_cmd python3

# shellcheck source=/dev/null
source "${REPO_ROOT}/scripts/local/contracts/load-runtime-env.sh"

export AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-openlakeforge}"
export AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-openlakeforge}"
export AWS_REGION="${AWS_REGION:-${OPENLAKEFORGE_STORAGE_REGION}}"
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-${AWS_REGION}}"
export AWS_ENDPOINT_URL_S3="${AWS_ENDPOINT_URL_S3:-${OPENLAKEFORGE_STORAGE_ENDPOINT}}"
export OPENLAKEFORGE_DUCKDB_S3_ENDPOINT="${OPENLAKEFORGE_DUCKDB_S3_ENDPOINT:-${OPENLAKEFORGE_STORAGE_ENDPOINT#http://}}"
export OPENLAKEFORGE_CATALOG_DBT_CLIENT_ID="${OPENLAKEFORGE_CATALOG_DBT_CLIENT_ID:-openlakeforge-dbt}"
export OPENLAKEFORGE_CATALOG_DBT_CLIENT_SECRET="${OPENLAKEFORGE_CATALOG_DBT_CLIENT_SECRET:-openlakeforge-dbt}"

discover_projects() {
  if [[ -n "${DBT_PROJECT_DIR:-}" ]]; then
    printf '%s\n' "${DBT_PROJECT_DIR}"
    return
  fi

  find domains -path "*/transformations/dbt/*/dbt_project.yml" -type f \
    -exec dirname {} \; | sort
}

projects=()
while IFS= read -r project_dir; do
  projects+=("${project_dir}")
done < <(discover_projects)
if [[ "${#projects[@]}" -eq 0 ]]; then
  echo "ERROR: no product dbt projects found." >&2
  exit 1
fi

for project_dir in "${projects[@]}"; do
  echo "==> Rendering dbt profile: ${project_dir}"
  python3 -m libs.dbt.render_profiles --project-dir "${project_dir}" --write

  echo "==> dbt deps: ${project_dir}"
  dbt deps --project-dir "${project_dir}"

  echo "==> dbt parse: ${project_dir}"
  dbt parse \
    --project-dir "${project_dir}" \
    --profiles-dir "${project_dir}" \
    --target local
done
