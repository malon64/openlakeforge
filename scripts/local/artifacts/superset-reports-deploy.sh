#!/usr/bin/env bash
# Deploy source-controlled Superset report assets into the local Superset instance.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
NAMESPACE="${NAMESPACE:-lakehouse}"
REPORT_WORK_DIR="${SUPERSET_REPORT_WORK_DIR:-.tmp/superset-reports}"
REPORTS_MOUNT_PATH="${SUPERSET_REPORTS_MOUNT_PATH:-/app/openlakeforge/reports}"
SUPERSET_ADMIN_USERNAME="${SUPERSET_ADMIN_USERNAME:-admin}"

# shellcheck source=/dev/null
source "${REPO_ROOT}/scripts/local/contracts/load-runtime-env.sh"

for cmd in kubectl python3; do
  if ! command -v "${cmd}" &>/dev/null; then
    echo "ERROR: '${cmd}' not found on PATH" >&2
    exit 1
  fi
done

discover_report_dirs() {
  if [[ -n "${SUPERSET_REPORT_SOURCE_DIR:-}" ]]; then
    printf '%s\n' "${SUPERSET_REPORT_SOURCE_DIR}"
    return
  fi

  find domains -path "*/reports/superset/*/metadata.yaml" -type f \
    -exec dirname {} \; | sort
}

echo "==> Waiting for Superset web deployment..."
kubectl rollout status deployment/superset -n "${NAMESPACE}" --timeout=300s

superset_pod="$(
  kubectl get pods -n "${NAMESPACE}" \
    -l app=superset,release=superset \
    -o jsonpath='{range .items[?(@.status.phase=="Running")]}{.metadata.name}{"\n"}{end}' \
    | head -n 1
)"

if [[ -z "${superset_pod}" ]]; then
  echo "ERROR: could not find a running Superset web pod." >&2
  exit 1
fi

deploy_report_dir() {
  local report_source_dir="$1"
  local bundle_root
  local bundle_name
  local bundle_path
  local remote_bundle

  if [[ ! -f "${report_source_dir}/metadata.yaml" ]]; then
    echo "ERROR: missing Superset report metadata at ${report_source_dir}/metadata.yaml" >&2
    exit 1
  fi

  bundle_root="$(echo "${report_source_dir}" | sed -E 's|^domains/||; s|/reports/superset/|_|; s|/|_|g')_superset_bundle"
  bundle_name="${bundle_root}.zip"
  bundle_path="${REPORT_WORK_DIR}/${bundle_name}"
  remote_bundle="${REPORTS_MOUNT_PATH}/${bundle_name}"

  mkdir -p "${REPORT_WORK_DIR}"

  python3 - "${report_source_dir}" "${bundle_path}" "${bundle_root}" "${OPENLAKEFORGE_QUERY_SQLALCHEMY_URI}" <<'PY'
import re
import sys
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile

source_dir = Path(sys.argv[1])
bundle_path = Path(sys.argv[2])
bundle_root = sys.argv[3]
sqlalchemy_uri = sys.argv[4]

with ZipFile(bundle_path, "w", ZIP_DEFLATED) as bundle:
    for path in sorted(source_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".yaml", ".yml"}:
            continue
        relative = path.relative_to(source_dir).as_posix()
        archive_name = PurePosixPath(bundle_root, relative).as_posix()
        if relative.startswith("databases/"):
            text = path.read_text(encoding="utf-8")
            text = re.sub(r"^sqlalchemy_uri:\s*.+$", f"sqlalchemy_uri: {sqlalchemy_uri}", text, flags=re.MULTILINE)
            bundle.writestr(archive_name, text)
        else:
            bundle.write(path, archive_name)
PY

  echo "==> Copying ${bundle_path} to ${superset_pod}:${remote_bundle}"
  kubectl exec -i "${superset_pod}" -c superset -n "${NAMESPACE}" -- \
    /bin/sh -ec "mkdir -p '${REPORTS_MOUNT_PATH}' && cat > '${remote_bundle}'" \
    < "${bundle_path}"

  echo "==> Importing Superset report assets from ${remote_bundle}"
  kubectl exec -i "${superset_pod}" -c superset -n "${NAMESPACE}" -- \
    /bin/sh -ec ". /app/pythonpath/superset_bootstrap.sh; python - '${remote_bundle}' '${SUPERSET_ADMIN_USERNAME}'" <<'PY'
import sys
from zipfile import ZipFile

from flask import g

from superset.app import create_app

bundle_path = sys.argv[1]
username = sys.argv[2]

app = create_app()
with app.app_context():
    from superset import security_manager
    from superset.commands.importers.v1.assets import ImportAssetsCommand
    from superset.commands.importers.v1.utils import get_contents_from_bundle

    user = security_manager.find_user(username=username)
    if user is None:
        raise SystemExit(f"Superset user '{username}' does not exist")

    g.user = user
    with ZipFile(bundle_path) as bundle:
        contents = get_contents_from_bundle(bundle)
    ImportAssetsCommand(contents).run()
PY

  echo "Deployed Superset report assets from ${report_source_dir}"
}

report_dirs=()
while IFS= read -r report_dir; do
  report_dirs+=("${report_dir}")
done < <(discover_report_dirs)
if [[ "${#report_dirs[@]}" -eq 0 ]]; then
  echo "ERROR: no product Superset report assets found." >&2
  exit 1
fi

for report_dir in "${report_dirs[@]}"; do
  deploy_report_dir "${report_dir}"
done
