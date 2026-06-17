#!/usr/bin/env bash
# Tear down the OpenLakeForge Azure POC platform. AKS and ACR are left intact.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
TERRAFORM_DIR="${REPO_ROOT}/infra/terraform/environments/azure-poc"
FOUNDATION_TERRAFORM_DIR="${REPO_ROOT}/infra/terraform/foundations/azure-aks"
FOUNDATION_STATE_PATH="${FOUNDATION_STATE_PATH:-${FOUNDATION_TERRAFORM_DIR}/terraform.tfstate}"
NAMESPACE="${NAMESPACE:-lakehouse}"
AZURE_CLUSTER_NAME="${AZURE_CLUSTER_NAME:-aks-openlakeforge-poc}"
KUBE_CONTEXT="${KUBE_CONTEXT:-}"

check_prereqs() {
  local missing=0
  for cmd in az kubectl terraform; do
    if ! command -v "${cmd}" &>/dev/null; then
      echo "ERROR: '${cmd}' not found on PATH" >&2
      missing=1
    fi
  done
  [[ "${missing}" -eq 0 ]] || exit 1
}

echo "==> Checking Azure teardown prerequisites..."
check_prereqs

if [[ ! -f "${FOUNDATION_STATE_PATH}" ]]; then
  echo "ERROR: Azure foundation Terraform state is missing: ${FOUNDATION_STATE_PATH}" >&2
  echo "The platform state depends on the foundation contract; restore or recreate it before running azure-down." >&2
  exit 1
fi

AZURE_RESOURCE_GROUP="$(terraform -chdir="${FOUNDATION_TERRAFORM_DIR}" output -raw resource_group_name)"
AZURE_CLUSTER_NAME="$(terraform -chdir="${FOUNDATION_TERRAFORM_DIR}" output -raw cluster_name)"
KUBE_CONTEXT="${KUBE_CONTEXT:-${AZURE_CLUSTER_NAME}}"

az aks get-credentials \
  --resource-group "${AZURE_RESOURCE_GROUP}" \
  --name "${AZURE_CLUSTER_NAME}" \
  --overwrite-existing >/dev/null

if ! kubectl cluster-info --context "${KUBE_CONTEXT}" >/dev/null 2>&1; then
  echo "ERROR: Kubernetes context '${KUBE_CONTEXT}' is not reachable." >&2
  echo "The platform must be destroyed before the Azure foundation is destroyed." >&2
  exit 1
fi
kubectl config use-context "${KUBE_CONTEXT}" >/dev/null

echo "==> Removing completed Superset init hook job if present..."
kubectl --context "${KUBE_CONTEXT}" delete job superset-init-db -n "${NAMESPACE}" \
  --ignore-not-found \
  --wait=true

echo "==> Destroying Terraform Azure POC stack..."
terraform -chdir="${TERRAFORM_DIR}" init
terraform -chdir="${TERRAFORM_DIR}" destroy \
  -auto-approve \
  -var="namespace=${NAMESPACE}" \
  -var="kube_context=${KUBE_CONTEXT}" \
  -var="foundation_state_path=${FOUNDATION_STATE_PATH}"

echo "==> Deleting namespace '${NAMESPACE}' if it still exists..."
kubectl --context "${KUBE_CONTEXT}" delete namespace "${NAMESPACE}" --ignore-not-found

echo "Azure POC platform teardown complete. AKS and ACR are still running."
echo "To delete the Azure foundation: make azure-foundation-down"
