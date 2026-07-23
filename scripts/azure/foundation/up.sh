#!/usr/bin/env bash
# Apply the Azure AKS foundation Terraform root and populate kubeconfig.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
TERRAFORM_DIR="${REPO_ROOT}/infra/terraform/foundations/azure-aks"
AZURE_CLUSTER_NAME="${AZURE_CLUSTER_NAME:-aks-openlakeforge-poc}"
AZURE_NODE_COUNT="${AZURE_NODE_COUNT:-3}"
AZURE_ACR_NAME_PREFIX="${AZURE_ACR_NAME_PREFIX:-openlakeforgepoc}"
KUBECONFIG_PATH="${KUBECONFIG_PATH:-${REPO_ROOT}/.tmp/kubeconfigs/azure.yaml}"
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

if [[ ! -f "${TFVARS_FILE}" ]]; then
  echo "ERROR: Azure foundation configuration not found: ${TFVARS_FILE}" >&2
  echo "Copy ${TERRAFORM_DIR}/sandbox.tfvars.example to ${TERRAFORM_DIR}/sandbox.tfvars and configure your resource group." >&2
  exit 1
fi

if ! az account show >/dev/null 2>&1; then
  echo "ERROR: Azure CLI is not logged in. Run 'az login' and select a subscription first." >&2
  exit 1
fi

echo "==> Initializing Terraform Azure AKS foundation..."
terraform -chdir="${TERRAFORM_DIR}" init

echo "==> Applying Terraform Azure AKS foundation..."
terraform -chdir="${TERRAFORM_DIR}" apply -auto-approve \
  -var-file="${TFVARS_FILE}" \
  -var="cluster_name=${AZURE_CLUSTER_NAME}" \
  -var="node_count=${AZURE_NODE_COUNT}" \
  -var="acr_name_prefix=${AZURE_ACR_NAME_PREFIX}" \
  -var="kubeconfig_path=${KUBECONFIG_PATH}"

resource_group="$(terraform -chdir="${TERRAFORM_DIR}" output -raw resource_group_name)"
cluster_name="$(terraform -chdir="${TERRAFORM_DIR}" output -raw cluster_name)"

echo "==> Fetching AKS kube credentials..."
mkdir -p "$(dirname "${KUBECONFIG_PATH}")"
az aks get-credentials \
  --resource-group "${resource_group}" \
  --name "${cluster_name}" \
  --file "${KUBECONFIG_PATH}" \
  --overwrite-existing

export KUBECONFIG="${KUBECONFIG_PATH}"
kubectl cluster-info --context "${cluster_name}" >/dev/null

echo ""
echo "Azure AKS foundation is ready."
echo "Kubernetes context: ${cluster_name}"
echo "ACR login server: $(terraform -chdir="${TERRAFORM_DIR}" output -raw acr_login_server)"
