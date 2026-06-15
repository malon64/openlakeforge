#!/usr/bin/env bash
# Apply the Azure AKS foundation Terraform root and populate kubeconfig.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
TERRAFORM_DIR="${REPO_ROOT}/infra/terraform/foundations/azure-aks"
AZURE_RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-rg-openlakeforge-azure-poc}"
AZURE_LOCATION="${AZURE_LOCATION:-westeurope}"
AZURE_CLUSTER_NAME="${AZURE_CLUSTER_NAME:-aks-openlakeforge-poc}"
AZURE_NODE_COUNT="${AZURE_NODE_COUNT:-3}"
AZURE_NODE_VM_SIZE="${AZURE_NODE_VM_SIZE:-Standard_D4s_v5}"
AZURE_ACR_NAME_PREFIX="${AZURE_ACR_NAME_PREFIX:-openlakeforgepoc}"

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

if ! az account show >/dev/null 2>&1; then
  echo "ERROR: Azure CLI is not logged in. Run 'az login' and select a subscription first." >&2
  exit 1
fi

echo "==> Initializing Terraform Azure AKS foundation..."
terraform -chdir="${TERRAFORM_DIR}" init

echo "==> Applying Terraform Azure AKS foundation..."
terraform -chdir="${TERRAFORM_DIR}" apply -auto-approve \
  -var="resource_group_name=${AZURE_RESOURCE_GROUP}" \
  -var="location=${AZURE_LOCATION}" \
  -var="cluster_name=${AZURE_CLUSTER_NAME}" \
  -var="node_count=${AZURE_NODE_COUNT}" \
  -var="node_vm_size=${AZURE_NODE_VM_SIZE}" \
  -var="acr_name_prefix=${AZURE_ACR_NAME_PREFIX}"

resource_group="$(terraform -chdir="${TERRAFORM_DIR}" output -raw resource_group_name)"
cluster_name="$(terraform -chdir="${TERRAFORM_DIR}" output -raw cluster_name)"

echo "==> Fetching AKS kube credentials..."
az aks get-credentials \
  --resource-group "${resource_group}" \
  --name "${cluster_name}" \
  --overwrite-existing

kubectl config use-context "${cluster_name}" >/dev/null
kubectl cluster-info --context "${cluster_name}" >/dev/null

echo ""
echo "Azure AKS foundation is ready."
echo "Kubernetes context: ${cluster_name}"
echo "ACR login server: $(terraform -chdir="${TERRAFORM_DIR}" output -raw acr_login_server)"
