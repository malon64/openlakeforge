#!/usr/bin/env bash
# Bring up the OpenLakeForge local stack on the current Kubernetes context.
#
# Terraform owns namespace creation, Helm releases, local credentials, bootstrap
# jobs, and service contracts. The kind cluster itself is still created by
# scripts/local/create-cluster.sh.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
TERRAFORM_DIR="${REPO_ROOT}/infra/terraform/environments/local"
NAMESPACE="${NAMESPACE:-lakehouse}"

check_prereqs() {
  local missing=0
  for cmd in terraform kubectl; do
    if ! command -v "${cmd}" &>/dev/null; then
      echo "ERROR: '${cmd}' not found on PATH" >&2
      missing=1
    fi
  done
  [[ $missing -eq 0 ]] || exit 1
}

echo "==> Checking prerequisites..."
check_prereqs

echo "==> Applying Terraform local stack..."
terraform -chdir="${TERRAFORM_DIR}" init
terraform -chdir="${TERRAFORM_DIR}" apply \
  -auto-approve \
  -var="namespace=${NAMESPACE}"

echo ""
echo "OpenLakeForge local stack is up."
echo ""
echo "Port-forward commands:"
echo "  kubectl port-forward svc/seaweedfs-s3 9000:8333 -n ${NAMESPACE}"
echo "  kubectl port-forward svc/polaris 8181:8181 -n ${NAMESPACE}"
echo "  kubectl port-forward svc/trino 8080:8080 -n ${NAMESPACE}"
echo ""
echo "Trino UI:     http://localhost:8080"
echo "Polaris API:  http://localhost:8181/api/catalog"
echo "SeaweedFS S3: http://localhost:9000"
