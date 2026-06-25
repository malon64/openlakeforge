#!/usr/bin/env bash
# Run AWS POC end-to-end validation against the EKS stack.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
FOUNDATION_TERRAFORM_DIR="${REPO_ROOT}/infra/terraform/foundations/aws-eks"
CONTRACT_TERRAFORM_DIR="${REPO_ROOT}/infra/terraform/environments/aws-poc"
NAMESPACE="${NAMESPACE:-lakehouse}"
KUBE_CONTEXT="${KUBE_CONTEXT:-}"

require_cmd() {
  local cmd="$1"
  if ! command -v "${cmd}" &>/dev/null; then
    echo "ERROR: '${cmd}' not found on PATH" >&2
    exit 1
  fi
}

for cmd in aws kubectl terraform python3; do
  require_cmd "${cmd}"
done

AWS_REGION="$(terraform -chdir="${FOUNDATION_TERRAFORM_DIR}" output -raw aws_region)"
AWS_CLUSTER_NAME="$(terraform -chdir="${FOUNDATION_TERRAFORM_DIR}" output -raw cluster_name)"
KUBE_CONTEXT="${KUBE_CONTEXT:-${AWS_CLUSTER_NAME}}"

aws eks update-kubeconfig \
  --region "${AWS_REGION}" \
  --name "${AWS_CLUSTER_NAME}" \
  --alias "${KUBE_CONTEXT}" >/dev/null
kubectl config use-context "${KUBE_CONTEXT}" >/dev/null

echo "==> Checking pod health..."
pods_json="$(mktemp)"
kubectl --context "${KUBE_CONTEXT}" get pods -n "${NAMESPACE}" -o json >"${pods_json}"
python3 - "${pods_json}" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text())
bad = []
for item in payload.get("items", []):
    name = item["metadata"]["name"]
    phase = item.get("status", {}).get("phase")
    if phase == "Succeeded":
        continue
    if phase == "Running":
        unready = [s["name"] for s in item.get("status", {}).get("containerStatuses", []) if not s.get("ready")]
        if not unready:
            continue
        bad.append(f"{name}: Running but containers not ready: {', '.join(unready)}")
        continue
    bad.append(f"{name}: {phase}")
if bad:
    raise SystemExit("ERROR: unhealthy pods:\n" + "\n".join(bad))
PY
rm -f "${pods_json}"

echo "==> Checking AWS provider contracts..."
contracts_json="$(mktemp)"
terraform -chdir="${CONTRACT_TERRAFORM_DIR}" output -json provider_contracts >"${contracts_json}"
python3 - "${contracts_json}" <<'PY'
import json
import sys
from pathlib import Path

contracts = json.loads(Path(sys.argv[1]).read_text())
expected = {
    ("storage", "implementation"): "storage.aws_s3",
    ("metadata_database", "implementation"): "metadata_database.aws_rds_postgresql",
    ("catalog", "implementation"): "catalog.aws_glue",
    ("catalog", "catalog_type"): "glue",
    ("artifacts", "implementation"): "artifacts.aws_ecr_and_s3",
}
for path, expected_value in expected.items():
    value = contracts
    for key in path:
        value = value[key]
    if value != expected_value:
        raise SystemExit(f"ERROR: provider_contracts.{'.'.join(path)} expected {expected_value!r}, got {value!r}")
PY

echo "==> Checking S3 artifact bucket and Glue databases..."
CODE_BUCKET="$(python3 - "${contracts_json}" <<'PY'
import json, sys
from pathlib import Path
print(json.loads(Path(sys.argv[1]).read_text())["artifact_bucket"]["bucket_name"])
PY
)"
aws s3api head-bucket --bucket "${CODE_BUCKET}" >/dev/null

python3 - "${contracts_json}" <<'PY' | while read -r database; do
import json, sys
from pathlib import Path
contracts = json.loads(Path(sys.argv[1]).read_text())
for database in contracts["catalog"]["catalog_namespaces"]:
    print(database["name"])
PY
  aws glue get-database --region "${AWS_REGION}" --name "${database}" >/dev/null
done

echo "==> Checking Trino Glue catalog..."
kubectl --context "${KUBE_CONTEXT}" exec -n "${NAMESPACE}" deploy/trino-coordinator -- \
  trino --output-format CSV_UNQUOTED --execute "SHOW CATALOGS" | grep -qx "iceberg"

echo "AWS POC e2e smoke validation passed."
