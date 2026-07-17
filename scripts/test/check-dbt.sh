#!/usr/bin/env bash
set -euo pipefail

require_cmd() {
  local cmd="$1"
  if ! command -v "${cmd}" &>/dev/null; then
    printf "ERROR: '%s' not found on PATH\n" "${cmd}" >&2
    exit 1
  fi
}

require_cmd python3

# shellcheck source=/dev/null
source "scripts/contracts/load-runtime-env.sh"

CACHE_ROOT="${DBT_CHECK_CACHE_DIR:-.cache/dbt-check}"
python_tag="$(python3 -c 'import sys; print(f"py{sys.version_info.major}{sys.version_info.minor}")')"
dependency_hash="$(python3 -c '
import hashlib
payload = "\n".join([
    "dbt-trino==1.10.2",
    "openlineage-dbt==1.45.0",
])
print(hashlib.sha256(payload.encode()).hexdigest()[:16])
')"
site_dir="${CACHE_ROOT}/${python_tag}-${dependency_hash}/site"
stamp_path="${CACHE_ROOT}/${python_tag}-${dependency_hash}/.complete"

if [[ ! -f "${stamp_path}" ]]; then
  rm -rf "${CACHE_ROOT:?}/${python_tag}-${dependency_hash}"
  mkdir -p "${site_dir}"

  echo "==> Installing dbt-trino check dependencies into ${site_dir}"
  PYTHONDONTWRITEBYTECODE=1 python3 -m pip install \
    --disable-pip-version-check \
    --no-compile \
    --prefer-binary \
    --target "${site_dir}" \
    "dbt-trino==1.10.2" \
    "openlineage-dbt==1.45.0"
  touch "${stamp_path}"
else
  echo "==> Reusing dbt dependency cache ${site_dir}"
fi

export PYTHONPATH="${site_dir}${PYTHONPATH:+:${PYTHONPATH}}"
export PATH="${site_dir}/bin:${PATH}"

export AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-openlakeforge}"
export AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-openlakeforge}"
export AWS_REGION="${AWS_REGION:-${OPENLAKEFORGE_STORAGE_REGION}}"
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-${AWS_REGION}}"
export AWS_ENDPOINT_URL_S3="${AWS_ENDPOINT_URL_S3:-${OPENLAKEFORGE_STORAGE_ENDPOINT}}"
export OPENLAKEFORGE_QUERY_TRINO_HOST="${OPENLAKEFORGE_QUERY_TRINO_HOST:-trino}"
export OPENLAKEFORGE_QUERY_TRINO_PORT="${OPENLAKEFORGE_QUERY_TRINO_PORT:-8080}"
export OPENLAKEFORGE_QUERY_TRINO_CATALOG="${OPENLAKEFORGE_QUERY_TRINO_CATALOG:-iceberg}"
export OPENLAKEFORGE_CATALOG_NAME="${OPENLAKEFORGE_CATALOG_NAME:-lakehouse_dev}"

projects=()
while IFS= read -r project_dir; do
  projects+=("${project_dir}")
done < <(
  find domains -path "*/transformations/dbt/*/dbt_project.yml" -type f \
    -exec dirname {} \; | sort
)

if [[ "${#projects[@]}" -eq 0 ]]; then
  echo "ERROR: no product dbt projects found." >&2
  exit 1
fi

for project_dir in "${projects[@]}"; do
  echo "==> Rendering dbt profile: ${project_dir}"
  python3 -m libs.dbt.render_profiles --environment local --project-dir "${project_dir}" --write

  echo "==> dbt deps: ${project_dir}"
  dbt deps --project-dir "${project_dir}"

  echo "==> dbt parse: ${project_dir}"
  dbt parse --project-dir "${project_dir}" --profiles-dir "${project_dir}" --target local

  echo "==> dbt compile: ${project_dir}"
  dbt compile --project-dir "${project_dir}" --profiles-dir "${project_dir}" --target local --no-introspect --no-populate-cache

  echo "==> dbt relation contract: ${project_dir}"
  python3 - "${project_dir}" <<'PY'
import json
import os
import sys
from pathlib import Path

project_dir = Path(sys.argv[1])
manifest_path = project_dir / "target" / "manifest.json"
expected_database = os.environ["OPENLAKEFORGE_CATALOG_NAME"]
parts = project_dir.parts
try:
    domain = parts[parts.index("domains") + 1]
except (ValueError, IndexError) as exc:
    raise SystemExit(f"Cannot derive domain from dbt project path: {project_dir}") from exc
expected_schema = f"{domain}_{project_dir.name}_gold"
expected_source_schema = f"{domain}_{project_dir.name}_silver"

manifest = json.loads(manifest_path.read_text())
violations = []
for node in manifest["nodes"].values():
    if node.get("resource_type") != "model":
        continue
    database = node.get("database")
    schema = node.get("schema")
    name = node.get("name")
    if database != expected_database or schema != expected_schema:
        violations.append(f"{name}: {database}.{schema}")

if violations:
    joined = ", ".join(violations)
    raise SystemExit(
        f"{project_dir} dbt models must compile to "
        f"{expected_database}.{expected_schema}.*; got {joined}"
    )

source_violations = []
for source in manifest["sources"].values():
    database = source.get("database")
    schema = source.get("schema")
    name = source.get("name")
    if database != expected_database or schema != expected_source_schema:
        source_violations.append(f"{name}: {database}.{schema}")

if source_violations:
    joined = ", ".join(source_violations)
    raise SystemExit(
        f"{project_dir} dbt Silver sources must compile to "
        f"{expected_database}.{expected_source_schema}.*; got {joined}"
    )
PY
done
