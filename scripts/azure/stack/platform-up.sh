#!/usr/bin/env bash
# Apply the static OpenLakeForge Azure POC platform on AKS.
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
POLARIS_BOOTSTRAP_GENERATION="${POLARIS_BOOTSTRAP_GENERATION:-manual}"
POLARIS_PREFLIGHT_LOG="${POLARIS_PREFLIGHT_LOG:-/tmp/openlakeforge-azure-polaris-port-forward.log}"
POLARIS_PREFLIGHT_BODY="${POLARIS_PREFLIGHT_BODY:-/tmp/openlakeforge-azure-polaris-token-check-body}"
TRINO_CHART_REPOSITORY="${TRINO_CHART_REPOSITORY:-https://trinodb.github.io/charts}"
TRINO_CHART_VERSION="${TRINO_CHART_VERSION:-1.42.2}"
DAGSTER_CHART_REPOSITORY="${DAGSTER_CHART_REPOSITORY:-https://dagster-io.github.io/helm}"
DAGSTER_CHART_VERSION="${DAGSTER_CHART_VERSION:-1.13.6}"

RUN_RETRY_ATTEMPTS="${AZURE_UP_RETRY_ATTEMPTS:-4}"
RUN_RETRY_DELAY_SECONDS="${AZURE_UP_RETRY_DELAY_SECONDS:-20}"

export NAMESPACE
export OPENLAKEFORGE_REPO_ROOT="${REPO_ROOT}"

# shellcheck source=scripts/lib/common.sh
source "${REPO_ROOT}/scripts/lib/common.sh"
# shellcheck source=scripts/lib/helm.sh
source "${REPO_ROOT}/scripts/lib/helm.sh"
# shellcheck source=scripts/lib/kube.sh
source "${REPO_ROOT}/scripts/lib/kube.sh"
# shellcheck source=scripts/lib/python.sh
source "${REPO_ROOT}/scripts/lib/python.sh"

TRINO_CHART_PACKAGE_PATH="${TRINO_CHART_PACKAGE_PATH:-${HELM_CHART_CACHE_DIR}/trino-${TRINO_CHART_VERSION}.tgz}"
DAGSTER_CHART_PACKAGE_PATH="${DAGSTER_CHART_PACKAGE_PATH:-${HELM_CHART_CACHE_DIR}/dagster-${DAGSTER_CHART_VERSION}-no-schema.tgz}"

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

terraform_apply_once() {
  cleanup_failed_jobs_by_prefix "polaris-bootstrap-"

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
    -var="polaris_bootstrap_generation=${POLARIS_BOOTSTRAP_GENERATION}" \
    -var="trino_chart_package_path=${TRINO_CHART_PACKAGE_PATH}" \
    -var="dagster_chart_package_path=${DAGSTER_CHART_PACKAGE_PATH}"
}

echo "==> Checking Azure platform prerequisites..."
check_prereqs az docker helm kubectl terraform uv base64
prepare_aks_context
prepare_image_variables
prepare_superset_image
prepare_helm_cache_dirs
prepare_cached_chart "Trino" trino "${TRINO_CHART_REPOSITORY}" trino/trino \
  "${TRINO_CHART_VERSION}" "${TRINO_CHART_PACKAGE_PATH}"
prepare_cached_dagster_chart_no_schema dagster "${DAGSTER_CHART_REPOSITORY}" \
  "${DAGSTER_CHART_VERSION}" "${DAGSTER_CHART_PACKAGE_PATH}"
prepare_polaris_bootstrap_generation

echo "==> Initializing Terraform Azure POC platform..."
terraform -chdir="${TERRAFORM_DIR}" init
terraform_import_namespace_args=(
  -var="namespace=${NAMESPACE}"
  -var="kube_context=${KUBE_CONTEXT}"
  -var="foundation_state_path=${FOUNDATION_STATE_PATH}"
  -var="project_code_image_repository=${PROJECT_CODE_IMAGE_REPOSITORY}"
  -var="project_code_image_tag=${PROJECT_CODE_IMAGE_TAG}"
  -var="project_code_image_pull_policy=${PROJECT_CODE_IMAGE_PULL_POLICY}"
  -var="project_code_image_revision=${PROJECT_CODE_IMAGE_REVISION}"
  -var="superset_image_repository=${SUPERSET_IMAGE_REPOSITORY}"
  -var="superset_image_tag=${SUPERSET_IMAGE_TAG}"
  -var="superset_image_pull_policy=${SUPERSET_IMAGE_PULL_POLICY}"
  -var="polaris_bootstrap_generation=${POLARIS_BOOTSTRAP_GENERATION}"
  -var="trino_chart_package_path=${TRINO_CHART_PACKAGE_PATH}"
  -var="dagster_chart_package_path=${DAGSTER_CHART_PACKAGE_PATH}"
)
import_namespace_if_missing_in_state \
  "${TERRAFORM_DIR}" \
  "kubernetes_namespace_v1.lakehouse" \
  "${NAMESPACE}" \
  "${terraform_import_namespace_args[@]}"

echo "==> Applying Terraform Azure POC platform..."
run_with_retry "Terraform apply" terraform_apply_once

echo "Static OpenLakeForge Azure POC platform is applied."
