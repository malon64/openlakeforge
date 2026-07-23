#!/usr/bin/env bash
# Deploy dynamic Azure POC artifacts after the static platform exists.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
FOUNDATION_TERRAFORM_DIR="${REPO_ROOT}/infra/terraform/foundations/azure-aks"
CONTRACT_TERRAFORM_DIR="${OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR:-${REPO_ROOT}/infra/terraform/environments/azure-poc}"
NAMESPACE="${NAMESPACE:-lakehouse}"
AZURE_CLUSTER_NAME="${AZURE_CLUSTER_NAME:-aks-openlakeforge-poc}"
KUBE_CONTEXT="${KUBE_CONTEXT:-}"
KUBECONFIG_PATH="${KUBECONFIG_PATH:-${REPO_ROOT}/.tmp/kubeconfigs/azure.yaml}"
DEPLOYMENT_SCOPE="${DEPLOYMENT_SCOPE:-azure}"
FLOE_RUNTIME_ARTIFACT_DIR="${FLOE_RUNTIME_ARTIFACT_DIR:-${REPO_ROOT}/.tmp/floe-runtime/azure}"
export OPENLAKEFORGE_REPO_ROOT="${REPO_ROOT}"
export OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR="${CONTRACT_TERRAFORM_DIR}"

# shellcheck source=scripts/lib/common.sh
source "${REPO_ROOT}/scripts/lib/common.sh"
# shellcheck source=scripts/lib/kube.sh
source "${REPO_ROOT}/scripts/lib/kube.sh"
# shellcheck source=scripts/lib/python.sh
source "${REPO_ROOT}/scripts/lib/python.sh"

cd "${REPO_ROOT}"

prepare_aks_context() {
  AZURE_RESOURCE_GROUP="$(terraform -chdir="${FOUNDATION_TERRAFORM_DIR}" output -raw resource_group_name)"
  AZURE_CLUSTER_NAME="$(terraform -chdir="${FOUNDATION_TERRAFORM_DIR}" output -raw cluster_name)"
  KUBE_CONTEXT="${KUBE_CONTEXT:-${AZURE_CLUSTER_NAME}}"
  configure_deployment_scope

  az aks get-credentials \
    --resource-group "${AZURE_RESOURCE_GROUP}" \
    --name "${AZURE_CLUSTER_NAME}" \
    --file "${KUBECONFIG_PATH}" \
    --overwrite-existing >/dev/null

  if ! kubectl cluster-info --context "${KUBE_CONTEXT}" >/dev/null 2>&1; then
    echo "ERROR: Kubernetes context '${KUBE_CONTEXT}' is not reachable." >&2
    echo "Run 'make azure-foundation-up' before deploying Azure artifacts." >&2
    exit 1
  fi

  require_kube_context
}

prepare_image_variables() {
  ACR_LOGIN_SERVER="$(terraform -chdir="${FOUNDATION_TERRAFORM_DIR}" output -raw acr_login_server)"
  ACR_NAME="$(terraform -chdir="${FOUNDATION_TERRAFORM_DIR}" output -raw acr_name)"
  AZURE_IMAGE_TAG="${AZURE_IMAGE_TAG:-azure-$(git_or_time_tag)}"
  PROJECT_CODE_IMAGE_REPOSITORY="${PROJECT_CODE_IMAGE_REPOSITORY:-${ACR_LOGIN_SERVER}/openlakeforge/project-code}"
  PROJECT_CODE_IMAGE_TAG="${PROJECT_CODE_IMAGE_TAG:-${AZURE_IMAGE_TAG}}"
  PROJECT_CODE_IMAGE="${PROJECT_CODE_IMAGE_REPOSITORY}:${PROJECT_CODE_IMAGE_TAG}"
}

for cmd in az docker kubectl uv terraform; do
  require_cmd "${cmd}"
done

prepare_aks_context
prepare_image_variables

# Load the provider contract environment for the olf artifact commands.
# shellcheck source=/dev/null
source "${REPO_ROOT}/scripts/contracts/load-runtime-env.sh"

echo "==> Generating product Floe manifests before baking the project-code image..."
export FLOE_RUNTIME_ARTIFACT_DIR
export FLOE_PERSIST_RUNTIME_ARTIFACTS="true"
NAMESPACE="${NAMESPACE}" bash "${REPO_ROOT}/scripts/artifacts/floe-manifest.sh"

echo "==> Building and pushing Azure project-code image..."
ACR_LOGIN_SERVER="${ACR_LOGIN_SERVER}" \
ACR_NAME="${ACR_NAME}" \
AZURE_IMAGE_TAG="${AZURE_IMAGE_TAG}" \
PROJECT_CODE_IMAGE_REPOSITORY="${PROJECT_CODE_IMAGE_REPOSITORY}" \
PROJECT_CODE_IMAGE_TAG="${PROJECT_CODE_IMAGE_TAG}" \
  bash "${SCRIPT_DIR}/../images/build-push-project-code.sh"

echo "==> Publishing product Floe manifests to the Azure POC SeaweedFS ops bucket..."
olf_run artifacts upload-manifests --via port-forward --runtime-root "${FLOE_RUNTIME_ARTIFACT_DIR}"

echo "==> Deploying product Superset report assets..."
olf_run superset deploy-reports

echo "==> Deploying OpenMetadata governance metadata..."
OPENMETADATA_ALLOW_MISSING_ASSETS="${OPENMETADATA_ALLOW_MISSING_ASSETS:-true}" \
  olf_run openmetadata deploy-metadata

echo "==> Pointing Dagster at project-code image ${PROJECT_CODE_IMAGE}..."
olf_run k8s set-project-code-image --image "${PROJECT_CODE_IMAGE}"

echo "Dynamic OpenLakeForge Azure POC artifacts are deployed."
