#!/usr/bin/env bash
# Deploy source-controlled Superset report assets into the local Superset instance.
set -euo pipefail

NAMESPACE="${NAMESPACE:-lakehouse}"
REPORT_SOURCE_DIR="${SUPERSET_REPORT_SOURCE_DIR:-domains/sales/reports/superset}"
REPORT_WORK_DIR="${SUPERSET_REPORT_WORK_DIR:-.tmp/superset-reports}"
REPORT_BUNDLE_ROOT="${SUPERSET_REPORT_BUNDLE_ROOT:-sales_superset_bundle}"
REPORT_BUNDLE_NAME="${SUPERSET_REPORT_BUNDLE_NAME:-sales_superset_assets.zip}"
REPORTS_MOUNT_PATH="${SUPERSET_REPORTS_MOUNT_PATH:-/app/openlakeforge/reports}"
SUPERSET_ADMIN_USERNAME="${SUPERSET_ADMIN_USERNAME:-admin}"

for cmd in kubectl python3; do
  if ! command -v "${cmd}" &>/dev/null; then
    echo "ERROR: '${cmd}' not found on PATH" >&2
    exit 1
  fi
done

if [[ ! -f "${REPORT_SOURCE_DIR}/metadata.yaml" ]]; then
  echo "ERROR: missing Superset report metadata at ${REPORT_SOURCE_DIR}/metadata.yaml" >&2
  exit 1
fi

mkdir -p "${REPORT_WORK_DIR}"
bundle_path="${REPORT_WORK_DIR}/${REPORT_BUNDLE_NAME}"

python3 - "${REPORT_SOURCE_DIR}" "${bundle_path}" "${REPORT_BUNDLE_ROOT}" <<'PY'
import sys
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile

source_dir = Path(sys.argv[1])
bundle_path = Path(sys.argv[2])
bundle_root = sys.argv[3]

with ZipFile(bundle_path, "w", ZIP_DEFLATED) as bundle:
    for path in sorted(source_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".yaml", ".yml"}:
            continue
        relative = path.relative_to(source_dir).as_posix()
        bundle.write(path, PurePosixPath(bundle_root, relative).as_posix())
PY

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

remote_bundle="${REPORTS_MOUNT_PATH}/${REPORT_BUNDLE_NAME}"

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

echo "Deployed Superset report assets from ${REPORT_SOURCE_DIR}"
