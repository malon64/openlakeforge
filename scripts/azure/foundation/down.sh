#!/usr/bin/env bash
# Destroy the Azure AKS foundation Terraform root.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
TERRAFORM_DIR="${REPO_ROOT}/infra/terraform/foundations/azure-aks"
NAMESPACE="${NAMESPACE:-lakehouse}"
AZURE_CLUSTER_NAME="${AZURE_CLUSTER_NAME:-aks-openlakeforge-poc}"
AZURE_NODE_COUNT="${AZURE_NODE_COUNT:-3}"
AZURE_ACR_NAME_PREFIX="${AZURE_ACR_NAME_PREFIX:-openlakeforgepoc}"
AZURE_FOUNDATION_FORCE_DOWN="${AZURE_FOUNDATION_FORCE_DOWN:-false}"
TFVARS_FILE="${AZURE_TFVARS_FILE:-${TERRAFORM_DIR}/sandbox.tfvars}"
if [[ "${TFVARS_FILE}" != /* ]]; then
  TFVARS_FILE="${REPO_ROOT}/${TFVARS_FILE}"
fi

check_prereqs() {
  local missing=0
  for cmd in az terraform kubectl; do
    if ! command -v "${cmd}" &>/dev/null; then
      echo "ERROR: '${cmd}' not found on PATH" >&2
      missing=1
    fi
  done
  [[ "${missing}" -eq 0 ]] || exit 1
}

echo "==> Checking Azure foundation prerequisites..."
check_prereqs

echo "==> Initializing Terraform Azure AKS foundation..."
terraform -chdir="${TERRAFORM_DIR}" init

if ! terraform -chdir="${TERRAFORM_DIR}" state list 2>/dev/null | grep -qx "azurerm_kubernetes_cluster.this"; then
  echo "No Azure AKS foundation state exists."
  exit 0
fi

if [[ ! -f "${TFVARS_FILE}" ]]; then
  echo "ERROR: Azure foundation configuration not found: ${TFVARS_FILE}" >&2
  echo "Restore the tfvars used to create this foundation, or set AZURE_TFVARS_FILE to its path." >&2
  exit 1
fi

cluster_name="$(terraform -chdir="${TERRAFORM_DIR}" output -raw cluster_name)"
resource_group="$(terraform -chdir="${TERRAFORM_DIR}" output -raw resource_group_name)"

if az aks show --resource-group "${resource_group}" --name "${cluster_name}" >/dev/null 2>&1; then
  az aks get-credentials --resource-group "${resource_group}" --name "${cluster_name}" --overwrite-existing >/dev/null
fi

if [[ "${AZURE_FOUNDATION_FORCE_DOWN}" != "true" ]] &&
  kubectl --context "${cluster_name}" get namespace "${NAMESPACE}" >/dev/null 2>&1; then
  echo "ERROR: namespace '${NAMESPACE}' still exists on '${cluster_name}'." >&2
  echo "Run 'make azure-platform-down' before destroying the Azure foundation." >&2
  echo "Set AZURE_FOUNDATION_FORCE_DOWN=true only if you intentionally want to delete the foundation with platform resources still present." >&2
  exit 1
fi

echo "==> Destroying Terraform Azure AKS foundation..."
terraform -chdir="${TERRAFORM_DIR}" destroy -auto-approve \
  -var-file="${TFVARS_FILE}" \
  -var="cluster_name=${AZURE_CLUSTER_NAME}" \
  -var="node_count=${AZURE_NODE_COUNT}" \
  -var="acr_name_prefix=${AZURE_ACR_NAME_PREFIX}"

echo "Azure AKS foundation is destroyed."
