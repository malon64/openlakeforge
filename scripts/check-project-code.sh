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

tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

build_dir="${tmp_dir}/build"
site_dir="${tmp_dir}/site"
mkdir -p "${build_dir}"

cp images/project-code/pyproject.toml "${build_dir}/pyproject.toml"
cp -R domains "${build_dir}/domains"
cp -R libs "${build_dir}/libs"

echo "==> Installing project-code package into an isolated target"
PYTHONDONTWRITEBYTECODE=1 python3 -m pip install \
  --disable-pip-version-check \
  --no-compile \
  --prefer-binary \
  --target "${site_dir}" \
  "${build_dir}"

echo "==> Loading Sales Dagster pipeline definitions"
PYTHONPATH="${site_dir}:${PWD}" python3 - <<'PY'
from domains.sales.extract.dlt.sales_poc import SALES_POC_ENTITIES
from domains.sales.pipelines.dagster.definitions import defs

defs.resolve_job_def("sales_bronze_to_silver_job")

asset_keys = {tuple(key.path) for asset_def in defs.assets for key in asset_def.keys}
for entity in SALES_POC_ENTITIES:
    if ("default", f"{entity}_source") not in asset_keys:
        raise SystemExit(f"missing Bronze source asset for {entity}")
    if ("default", entity) not in asset_keys:
        raise SystemExit(f"missing Floe Silver asset for {entity}")

print("Project-code Sales pipeline definitions loaded.")
PY
