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
python3 -m pip install --disable-pip-version-check --target "${site_dir}" "${build_dir}"

echo "==> Executing Iteration 2 Dagster smoke job in-process"
PYTHONPATH="${site_dir}:${PWD}" python3 - <<'PY'
from domains.sales.pipelines.dagster.definitions import defs, iteration2_smoke_job

result = iteration2_smoke_job.execute_in_process()
if not result.success:
    raise SystemExit("iteration2_smoke_job failed")

defs.get_job_def("iteration3_sales_silver_job")

print("Project-code smoke job passed.")
PY
