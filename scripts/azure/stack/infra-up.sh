#!/usr/bin/env bash
# Apply the static OpenLakeForge Azure POC infrastructure on AKS.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
TERRAFORM_DIR="${REPO_ROOT}/infra/terraform/environments/azure-poc"
FOUNDATION_TERRAFORM_DIR="${REPO_ROOT}/infra/terraform/foundations/azure-aks"
FOUNDATION_STATE_PATH="${FOUNDATION_STATE_PATH:-${FOUNDATION_TERRAFORM_DIR}/terraform.tfstate}"
NAMESPACE="${NAMESPACE:-lakehouse}"
AZURE_CLUSTER_NAME="${AZURE_CLUSTER_NAME:-aks-openlakeforge-poc}"
KUBE_CONTEXT="${KUBE_CONTEXT:-}"
PROJECT_CODE_IMAGE_PULL_POLICY="${PROJECT_CODE_IMAGE_PULL_POLICY:-Always}"
PROJECT_CODE_IMAGE_REVISION="${PROJECT_CODE_IMAGE_REVISION:-manual}"
SUPERSET_IMAGE_PULL_POLICY="${SUPERSET_IMAGE_PULL_POLICY:-Always}"
HELM_REPOSITORY_CONFIG="${HELM_REPOSITORY_CONFIG:-${REPO_ROOT}/.tmp/helm/repositories.yaml}"
HELM_REPOSITORY_CACHE="${HELM_REPOSITORY_CACHE:-${REPO_ROOT}/.tmp/helm/repository-cache}"
HELM_CHART_CACHE_DIR="${HELM_CHART_CACHE_DIR:-${REPO_ROOT}/.tmp/helm/charts}"
TRINO_CHART_REPOSITORY="${TRINO_CHART_REPOSITORY:-https://trinodb.github.io/charts}"
TRINO_CHART_VERSION="${TRINO_CHART_VERSION:-1.42.2}"
TRINO_CHART_PACKAGE_PATH="${TRINO_CHART_PACKAGE_PATH:-${HELM_CHART_CACHE_DIR}/trino-${TRINO_CHART_VERSION}.tgz}"

git_or_time_tag() {
  git -C "${REPO_ROOT}" rev-parse --short HEAD 2>/dev/null || date -u +%Y%m%d%H%M%S
}

check_prereqs() {
  local missing=0
  for cmd in az docker helm kubectl terraform; do
    if ! command -v "${cmd}" &>/dev/null; then
      echo "ERROR: '${cmd}' not found on PATH" >&2
      missing=1
    fi
  done
  [[ "${missing}" -eq 0 ]] || exit 1
}

run_with_retry() {
  local description="$1"
  shift

  local max_attempts="${AZURE_UP_RETRY_ATTEMPTS:-4}"
  local delay_seconds="${AZURE_UP_RETRY_DELAY_SECONDS:-20}"
  local attempt=1

  while true; do
    if "$@"; then
      return 0
    else
      local status=$?
    fi

    if ((attempt >= max_attempts)); then
      echo "ERROR: ${description} failed after ${attempt} attempt(s)." >&2
      return "${status}"
    fi

    echo "WARN: ${description} failed on attempt ${attempt}/${max_attempts}; retrying in ${delay_seconds}s..." >&2
    sleep "${delay_seconds}"
    attempt=$((attempt + 1))
  done
}

helm_cached() {
  HELM_REPOSITORY_CONFIG="${HELM_REPOSITORY_CONFIG}" \
    HELM_REPOSITORY_CACHE="${HELM_REPOSITORY_CACHE}" \
    helm "$@"
}

prepare_aks_context() {
  if [[ ! -f "${FOUNDATION_STATE_PATH}" ]]; then
    echo "ERROR: Azure foundation Terraform state is missing: ${FOUNDATION_STATE_PATH}" >&2
    echo "Run 'make azure-foundation-up' before applying the Azure platform." >&2
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
    echo "Run 'make azure-foundation-up' before applying the Azure platform." >&2
    exit 1
  fi

  kubectl config use-context "${KUBE_CONTEXT}" >/dev/null
}

prepare_helm_chart_cache() {
  mkdir -p "$(dirname "${HELM_REPOSITORY_CONFIG}")" "${HELM_REPOSITORY_CACHE}" "${HELM_CHART_CACHE_DIR}"

  if [[ -f "${TRINO_CHART_PACKAGE_PATH}" ]] && helm show chart "${TRINO_CHART_PACKAGE_PATH}" >/dev/null 2>&1; then
    echo "==> Using cached Trino Helm chart: ${TRINO_CHART_PACKAGE_PATH}"
    return 0
  fi

  rm -f "${TRINO_CHART_PACKAGE_PATH}"

  echo "==> Downloading Trino Helm chart ${TRINO_CHART_VERSION} into local cache..."
  run_with_retry "Helm repo add Trino" \
    helm_cached repo add trino "${TRINO_CHART_REPOSITORY}" --force-update
  run_with_retry "Helm repo update" \
    helm_cached repo update
  run_with_retry "Trino Helm chart download" \
    helm_cached pull trino/trino --version "${TRINO_CHART_VERSION}" --destination "${HELM_CHART_CACHE_DIR}"
}

prepare_image_variables() {
  ACR_LOGIN_SERVER="$(terraform -chdir="${FOUNDATION_TERRAFORM_DIR}" output -raw acr_login_server)"
  ACR_NAME="$(terraform -chdir="${FOUNDATION_TERRAFORM_DIR}" output -raw acr_name)"
  AZURE_IMAGE_TAG="${AZURE_IMAGE_TAG:-azure-$(git_or_time_tag)}"
  PROJECT_CODE_IMAGE_REPOSITORY="${PROJECT_CODE_IMAGE_REPOSITORY:-${ACR_LOGIN_SERVER}/openlakeforge/project-code}"
  PROJECT_CODE_IMAGE_TAG="${PROJECT_CODE_IMAGE_TAG:-${AZURE_IMAGE_TAG}}"
  SUPERSET_IMAGE_REPOSITORY="${SUPERSET_IMAGE_REPOSITORY:-${ACR_LOGIN_SERVER}/openlakeforge/superset}"
  SUPERSET_IMAGE_TAG="${SUPERSET_IMAGE_TAG:-${AZURE_IMAGE_TAG}}"
}

prepare_superset_image() {
  echo "==> Building and pushing Azure Superset image before Helm install..."
  ACR_LOGIN_SERVER="${ACR_LOGIN_SERVER}" \
  ACR_NAME="${ACR_NAME}" \
  AZURE_IMAGE_TAG="${AZURE_IMAGE_TAG}" \
  SUPERSET_IMAGE_REPOSITORY="${SUPERSET_IMAGE_REPOSITORY}" \
  SUPERSET_IMAGE_TAG="${SUPERSET_IMAGE_TAG}" \
    bash "${SCRIPT_DIR}/../images/build-push-superset.sh"
}

terraform_apply_with_retry() {
  run_with_retry "Terraform apply" \
    terraform -chdir="${TERRAFORM_DIR}" apply -auto-approve \
      -var="namespace=${NAMESPACE}" \
      -var="kube_context=${KUBE_CONTEXT}" \
      -var="foundation_state_path=${FOUNDATION_STATE_PATH}" \
      -var="project_code_image_repository=${PROJECT_CODE_IMAGE_REPOSITORY}" \
      -var="project_code_image_tag=${PROJECT_CODE_IMAGE_TAG}" \
      -var="project_code_image_pull_policy=${PROJECT_CODE_IMAGE_PULL_POLICY}" \
      -var="project_code_image_revision=${PROJECT_CODE_IMAGE_REVISION}" \
      -var="superset_image_repository=${SUPERSET_IMAGE_REPOSITORY}" \
      -var="superset_image_tag=${SUPERSET_IMAGE_TAG}" \
      -var="superset_image_pull_policy=${SUPERSET_IMAGE_PULL_POLICY}" \
      -var="trino_chart_package_path=${TRINO_CHART_PACKAGE_PATH}"
}

echo "==> Checking Azure infrastructure prerequisites..."
check_prereqs
prepare_aks_context
prepare_image_variables
prepare_superset_image
prepare_helm_chart_cache

echo "==> Initializing Terraform Azure POC platform..."
terraform -chdir="${TERRAFORM_DIR}" init

echo "==> Applying Terraform Azure POC infrastructure..."
terraform_apply_with_retry

echo "Static OpenLakeForge Azure POC infrastructure is applied."
