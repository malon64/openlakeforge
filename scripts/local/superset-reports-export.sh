#!/usr/bin/env bash
# Export the local Sales Superset dashboard back into the source-controlled bundle.
set -euo pipefail

NAMESPACE="${NAMESPACE:-lakehouse}"
REPORT_SOURCE_DIR="${SUPERSET_REPORT_SOURCE_DIR:-domains/sales/reports/superset}"
REPORT_WORK_DIR="${SUPERSET_REPORT_WORK_DIR:-.tmp/superset-reports}"
REPORT_BUNDLE_ROOT="${SUPERSET_REPORT_BUNDLE_ROOT:-sales_superset_bundle}"
REPORT_BUNDLE_NAME="${SUPERSET_REPORT_EXPORT_BUNDLE_NAME:-sales_superset_assets_export.zip}"
REPORTS_MOUNT_PATH="${SUPERSET_REPORTS_MOUNT_PATH:-/app/openlakeforge/reports}"
SUPERSET_ADMIN_USERNAME="${SUPERSET_ADMIN_USERNAME:-admin}"
SUPERSET_DASHBOARD_TITLE="${SUPERSET_DASHBOARD_TITLE:-Sales Gold Mart Overview}"

for cmd in kubectl python3; do
  if ! command -v "${cmd}" &>/dev/null; then
    echo "ERROR: '${cmd}' not found on PATH" >&2
    exit 1
  fi
done

mkdir -p "${REPORT_WORK_DIR}"
local_bundle="${REPORT_WORK_DIR}/${REPORT_BUNDLE_NAME}"
remote_bundle="${REPORTS_MOUNT_PATH}/${REPORT_BUNDLE_NAME}"

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

echo "==> Exporting '${SUPERSET_DASHBOARD_TITLE}' from Superset"
kubectl exec -i "${superset_pod}" -c superset -n "${NAMESPACE}" -- \
  /bin/sh -ec ". /app/pythonpath/superset_bootstrap.sh; python - '${remote_bundle}' '${SUPERSET_ADMIN_USERNAME}' '${SUPERSET_DASHBOARD_TITLE}' '${REPORT_BUNDLE_ROOT}'" <<'PY'
import sys
from zipfile import ZIP_DEFLATED, ZipFile

import yaml
from flask import g

from superset import security_manager
from superset.app import create_app
from superset.commands.dashboard.export import ExportDashboardsCommand
from superset.extensions import db
from superset.models.dashboard import Dashboard

bundle_path = sys.argv[1]
username = sys.argv[2]
dashboard_title = sys.argv[3]
bundle_root = sys.argv[4]

app = create_app()
with app.app_context():
    user = security_manager.find_user(username=username)
    if user is None:
        raise SystemExit(f"Superset user '{username}' does not exist")
    g.user = user

    dashboard_ids = [
        dashboard_id
        for (dashboard_id,) in db.session.query(Dashboard.id)
        .filter(Dashboard.dashboard_title == dashboard_title)
        .all()
    ]
    if not dashboard_ids:
        raise SystemExit(f"Superset dashboard '{dashboard_title}' does not exist")

    with ZipFile(bundle_path, "w", ZIP_DEFLATED) as bundle:
        for file_name, file_content in ExportDashboardsCommand(dashboard_ids).run():
            content = file_content()
            if file_name == "metadata.yaml":
                metadata = yaml.safe_load(content)
                metadata["type"] = "assets"
                content = yaml.safe_dump(metadata, sort_keys=False)
            with bundle.open(f"{bundle_root}/{file_name}", "w") as fp:
                fp.write(content.encode())
PY

kubectl exec "${superset_pod}" -c superset -n "${NAMESPACE}" -- \
  cat "${remote_bundle}" > "${local_bundle}"

python3 - "${local_bundle}" "${REPORT_SOURCE_DIR}" <<'PY'
import shutil
import sys
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

bundle_path = Path(sys.argv[1])
target_dir = Path(sys.argv[2])
managed_names = {"metadata.yaml", "databases", "datasets", "charts", "dashboards"}

target_dir.mkdir(parents=True, exist_ok=True)
for name in managed_names:
    path = target_dir / name
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()

with ZipFile(bundle_path) as bundle:
    for member in bundle.namelist():
        path = PurePosixPath(member)
        if len(path.parts) < 2 or path.name.startswith(".") or path.suffix.lower() not in {".yaml", ".yml"}:
            continue
        relative = PurePosixPath(*path.parts[1:])
        destination = target_dir / Path(relative.as_posix())
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(bundle.read(member))
PY

echo "Exported Superset report assets to ${REPORT_SOURCE_DIR}"
