#!/usr/bin/env bash
# Deploy dynamic Azure POC artifacts after the static infrastructure exists.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
FOUNDATION_TERRAFORM_DIR="${REPO_ROOT}/infra/terraform/foundations/azure-aks"
CONTRACT_TERRAFORM_DIR="${OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR:-${REPO_ROOT}/infra/terraform/environments/azure-poc}"
NAMESPACE="${NAMESPACE:-lakehouse}"
AZURE_CLUSTER_NAME="${AZURE_CLUSTER_NAME:-aks-openlakeforge-poc}"
KUBE_CONTEXT="${KUBE_CONTEXT:-}"

git_or_time_tag() {
  git -C "${REPO_ROOT}" rev-parse --short HEAD 2>/dev/null || date -u +%Y%m%d%H%M%S
}

require_cmd() {
  local cmd="$1"
  if ! command -v "${cmd}" &>/dev/null; then
    echo "ERROR: '${cmd}' not found on PATH" >&2
    exit 1
  fi
}

prepare_aks_context() {
  AZURE_RESOURCE_GROUP="$(terraform -chdir="${FOUNDATION_TERRAFORM_DIR}" output -raw resource_group_name)"
  AZURE_CLUSTER_NAME="$(terraform -chdir="${FOUNDATION_TERRAFORM_DIR}" output -raw cluster_name)"
  KUBE_CONTEXT="${KUBE_CONTEXT:-${AZURE_CLUSTER_NAME}}"

  az aks get-credentials \
    --resource-group "${AZURE_RESOURCE_GROUP}" \
    --name "${AZURE_CLUSTER_NAME}" \
    --overwrite-existing >/dev/null

  if ! kubectl cluster-info --context "${KUBE_CONTEXT}" >/dev/null 2>&1; then
    echo "ERROR: Kubernetes context '${KUBE_CONTEXT}' is not reachable." >&2
    echo "Run 'make azure-foundation-up' before deploying Azure artifacts." >&2
    exit 1
  fi

  kubectl config use-context "${KUBE_CONTEXT}" >/dev/null
}

prepare_image_variables() {
  ACR_LOGIN_SERVER="$(terraform -chdir="${FOUNDATION_TERRAFORM_DIR}" output -raw acr_login_server)"
  ACR_NAME="$(terraform -chdir="${FOUNDATION_TERRAFORM_DIR}" output -raw acr_name)"
  AZURE_IMAGE_TAG="${AZURE_IMAGE_TAG:-azure-$(git_or_time_tag)}"
  PROJECT_CODE_IMAGE_REPOSITORY="${PROJECT_CODE_IMAGE_REPOSITORY:-${ACR_LOGIN_SERVER}/openlakeforge/project-code}"
  PROJECT_CODE_IMAGE_TAG="${PROJECT_CODE_IMAGE_TAG:-${AZURE_IMAGE_TAG}}"
}

restart_if_exists() {
  local deployment="$1"

  if ! kubectl get deployment "${deployment}" -n "${NAMESPACE}" >/dev/null 2>&1; then
    return 0
  fi

  echo "==> Restarting ${deployment} after Azure artifact deployment..."
  kubectl rollout restart "deployment/${deployment}" -n "${NAMESPACE}"
  kubectl rollout status "deployment/${deployment}" -n "${NAMESPACE}" --timeout=600s
}

restart_dagster_project_code_deployments() {
  restart_if_exists "dagster-dagster-webserver"
  restart_if_exists "dagster-dagster-daemon"
  restart_if_exists "dagster-dagster-user-deployments-openlakeforge-dagster"
  restart_if_exists "dagster-webserver"
  restart_if_exists "dagster-daemon"
  restart_if_exists "dagster-user-deployments-openlakeforge-dagster"
}

for cmd in az docker kubectl python3 terraform; do
  require_cmd "${cmd}"
done

prepare_aks_context
prepare_image_variables

echo "==> Generating product Floe manifests before baking the project-code image..."
NAMESPACE="${NAMESPACE}" \
OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR="${CONTRACT_TERRAFORM_DIR}" \
  bash "${REPO_ROOT}/scripts/local/artifacts/floe-manifest.sh"

echo "==> Building and pushing Azure project-code image..."
ACR_LOGIN_SERVER="${ACR_LOGIN_SERVER}" \
ACR_NAME="${ACR_NAME}" \
AZURE_IMAGE_TAG="${AZURE_IMAGE_TAG}" \
PROJECT_CODE_IMAGE_REPOSITORY="${PROJECT_CODE_IMAGE_REPOSITORY}" \
PROJECT_CODE_IMAGE_TAG="${PROJECT_CODE_IMAGE_TAG}" \
  bash "${SCRIPT_DIR}/../images/build-push-project-code.sh"

echo "==> Publishing product Floe manifests to the Azure POC SeaweedFS code bucket..."
NAMESPACE="${NAMESPACE}" \
OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR="${CONTRACT_TERRAFORM_DIR}" \
  bash "${REPO_ROOT}/scripts/local/artifacts/upload-floe-manifest.sh"

echo "==> Deploying product Superset report assets..."
NAMESPACE="${NAMESPACE}" \
OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR="${CONTRACT_TERRAFORM_DIR}" \
  bash "${REPO_ROOT}/scripts/local/artifacts/superset-reports-deploy.sh"

echo "==> Deploying OpenMetadata governance metadata..."
NAMESPACE="${NAMESPACE}" \
OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR="${CONTRACT_TERRAFORM_DIR}" \
OPENMETADATA_ALLOW_MISSING_ASSETS="${OPENMETADATA_ALLOW_MISSING_ASSETS:-true}" \
  bash "${REPO_ROOT}/scripts/local/artifacts/openmetadata-metadata-deploy.sh"

restart_dagster_project_code_deployments

echo "Dynamic OpenLakeForge Azure POC artifacts are deployed."
