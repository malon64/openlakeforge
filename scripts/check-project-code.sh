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

echo "==> Installing project-code package into an isolated target"
python3 -m pip install --disable-pip-version-check --target "${tmp_dir}" .

echo "==> Executing Iteration 2 Dagster smoke job in-process"
PYTHONPATH="${tmp_dir}:${PWD}" python3 - <<'PY'
from domains.sales.orchestration.dagster.definitions import iteration2_smoke_job

result = iteration2_smoke_job.execute_in_process()
if not result.success:
    raise SystemExit("iteration2_smoke_job failed")

print("Project-code smoke job passed.")
PY
