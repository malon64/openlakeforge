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

CACHE_ROOT="${PROJECT_CODE_CHECK_CACHE_DIR:-.cache/project-code-check}"
python_tag="$(python3 - <<'PY'
import sys
print(f"py{sys.version_info.major}{sys.version_info.minor}")
PY
)"
pyproject_hash="$(python3 - <<'PY'
import hashlib
from pathlib import Path
print(hashlib.sha256(Path("images/project-code/pyproject.toml").read_bytes()).hexdigest()[:16])
PY
)"
site_dir="${CACHE_ROOT}/${python_tag}-${pyproject_hash}/site"
stamp_path="${CACHE_ROOT}/${python_tag}-${pyproject_hash}/.complete"

if [[ ! -f "${stamp_path}" ]]; then
  rm -rf "${CACHE_ROOT:?}/${python_tag}-${pyproject_hash}"
  mkdir -p "${site_dir}"

  mapfile -t dependencies < <(python3 - <<'PY'
import tomllib
from pathlib import Path

with Path("images/project-code/pyproject.toml").open("rb") as fh:
    payload = tomllib.load(fh)

for dependency in payload["project"]["dependencies"]:
    print(dependency)
PY
  )

  echo "==> Installing project-code dependencies into ${site_dir}"
  PYTHONDONTWRITEBYTECODE=1 python3 -m pip install \
    --disable-pip-version-check \
    --no-compile \
    --prefer-binary \
    --target "${site_dir}" \
    "${dependencies[@]}"
  touch "${stamp_path}"
else
  echo "==> Reusing project-code dependency cache ${site_dir}"
fi

echo "==> Loading Sales Dagster pipeline definitions"
PATH="${site_dir}/bin:${PATH}" PYTHONPATH="${site_dir}:${PWD}" python3 - <<'PY'
from pathlib import Path

from floe_dagster.manifest import load_manifest

from domains.sales.extract.dlt.sales_poc import SALES_POC_ENTITIES
from domains.sales.pipelines.dagster.definitions import defs

defs.resolve_job_def("sales_bronze_to_silver_job")
defs.resolve_job_def("sales_bronze_to_gold_job")

manifest = load_manifest(Path("domains/sales/contracts/floe/manifests/sales.manifest.json"))
if manifest.execution.base_args != [
    "run",
    "--manifest",
    "{manifest_uri}",
    "--log-format",
    "json",
    "--quiet",
]:
    raise SystemExit("Sales Floe manifest does not use the runtime manifest_uri placeholder")

asset_keys = {
    tuple(key.path)
    for asset_def in defs.assets
    if hasattr(asset_def, "keys")
    for key in asset_def.keys
}
source_asset_keys = {
    tuple(asset.key.path) for asset in defs.assets if not hasattr(asset, "keys")
}
for entity in SALES_POC_ENTITIES:
    if ("sales", f"{entity}_source") not in asset_keys:
        raise SystemExit(f"missing Bronze source asset for {entity}")
    if ("sales", entity) not in asset_keys:
        raise SystemExit(f"missing Floe Silver asset for {entity}")
    if ("sales", f"{entity}_source") in source_asset_keys:
        raise SystemExit(f"Floe registered a duplicate source asset for {entity}")
    matching_entities = [item for item in manifest.entities if item.name == entity]
    if not matching_entities:
        raise SystemExit(f"missing Floe manifest entity for {entity}")
    if matching_entities[0].group_name != "sales":
        raise SystemExit(f"Floe manifest entity {entity} is not in the sales group")

for asset_name in [
    "mart_sales_by_day",
    "mart_revenue_by_product",
    "mart_sales_by_customer",
    "sales_gold_trino_smoke_test",
]:
    if ("sales", asset_name) not in asset_keys:
        raise SystemExit(f"missing dbt Gold or smoke-test asset {asset_name}")

print("Project-code Sales pipeline definitions loaded.")
PY
