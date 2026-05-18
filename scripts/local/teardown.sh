#!/usr/bin/env bash
# Tear down the OpenLakeForge local stack. The kind cluster itself is left
# intact.
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

echo "==> Destroying Terraform local stack..."
terraform -chdir="${TERRAFORM_DIR}" init
terraform -chdir="${TERRAFORM_DIR}" destroy \
  -auto-approve \
  -var="namespace=${NAMESPACE}"

# One-time compatibility cleanup for stacks that were deployed before Terraform
# owned the local environment.
if command -v helm &>/dev/null; then
  echo "==> Removing legacy unmanaged Helm releases if present..."
  for release in trino polaris seaweedfs garage; do
    if helm status "${release}" -n "${NAMESPACE}" &>/dev/null; then
      helm uninstall "${release}" -n "${NAMESPACE}"
    fi
  done
fi

echo "==> Deleting namespace '${NAMESPACE}' if it still exists..."
kubectl delete namespace "${NAMESPACE}" --ignore-not-found

echo "Teardown complete. Kind cluster is still running."
echo "To delete the cluster: make local-destroy-cluster"
