#!/usr/bin/env bash
# Apply the static OpenLakeForge local platform.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
TERRAFORM_DIR="${REPO_ROOT}/infra/terraform/environments/local"
FOUNDATION_TERRAFORM_DIR="${REPO_ROOT}/infra/terraform/foundations/local-kind"
FOUNDATION_STATE_PATH="${FOUNDATION_STATE_PATH:-${FOUNDATION_TERRAFORM_DIR}/terraform.tfstate}"
NAMESPACE="${NAMESPACE:-lakehouse}"
export NAMESPACE
CLUSTER_NAME="${CLUSTER_NAME:-openlakeforge-local}"
KUBE_CONTEXT="${KUBE_CONTEXT:-kind-${CLUSTER_NAME}}"
KUBECONFIG_PATH="${KUBECONFIG_PATH:-${REPO_ROOT}/.tmp/kubeconfigs/local.yaml}"
DEPLOYMENT_SCOPE="${DEPLOYMENT_SCOPE:-local}"
export OPENLAKEFORGE_REPO_ROOT="${REPO_ROOT}"
PROJECT_CODE_IMAGE_REPOSITORY="${PROJECT_CODE_IMAGE_REPOSITORY:-ghcr.io/openlakeforge/project-code}"
PROJECT_CODE_IMAGE_TAG="${PROJECT_CODE_IMAGE_TAG:-local}"
PROJECT_CODE_IMAGE_PULL_POLICY="${PROJECT_CODE_IMAGE_PULL_POLICY:-Never}"
PROJECT_CODE_IMAGE_REVISION="${PROJECT_CODE_IMAGE_REVISION:-manual}"
SUPERSET_IMAGE_REPOSITORY="${SUPERSET_IMAGE_REPOSITORY:-ghcr.io/openlakeforge/superset}"
SUPERSET_IMAGE_TAG="${SUPERSET_IMAGE_TAG:-local}"
SUPERSET_IMAGE_PULL_POLICY="${SUPERSET_IMAGE_PULL_POLICY:-Never}"
TRINO_CHART_REPOSITORY="${TRINO_CHART_REPOSITORY:-https://trinodb.github.io/charts}"
TRINO_CHART_VERSION="${TRINO_CHART_VERSION:-1.42.2}"

RUN_RETRY_ATTEMPTS="${LOCAL_UP_RETRY_ATTEMPTS:-4}"
RUN_RETRY_DELAY_SECONDS="${LOCAL_UP_RETRY_DELAY_SECONDS:-20}"

# shellcheck source=scripts/lib/common.sh
source "${REPO_ROOT}/scripts/lib/common.sh"
configure_deployment_scope
# shellcheck source=scripts/lib/helm.sh
source "${REPO_ROOT}/scripts/lib/helm.sh"
# shellcheck source=scripts/lib/kube.sh
source "${REPO_ROOT}/scripts/lib/kube.sh"
# shellcheck source=scripts/lib/python.sh
source "${REPO_ROOT}/scripts/lib/python.sh"

TRINO_CHART_PACKAGE_PATH="${TRINO_CHART_PACKAGE_PATH:-${HELM_CHART_CACHE_DIR}/trino-${TRINO_CHART_VERSION}.tgz}"

check_cluster() {
  if [[ ! -f "${FOUNDATION_STATE_PATH}" ]]; then
    echo "ERROR: local foundation Terraform state is missing: ${FOUNDATION_STATE_PATH}" >&2
    echo "Run 'make local-foundation-up' before applying the local platform." >&2
    exit 1
  fi

  if ! require_kube_context >/dev/null 2>&1; then
    echo "ERROR: Kubernetes context '${KUBE_CONTEXT}' is not reachable." >&2
    echo "Run 'make local-foundation-up' before applying the local platform." >&2
    exit 1
  fi
}

prepare_local_superset_image() {
  if [[ "${SUPERSET_IMAGE_TAG}" != "local" ]]; then
    return 0
  fi

  if ! command -v docker &>/dev/null; then
    echo "ERROR: Docker is required when SUPERSET_IMAGE_TAG=local." >&2
    echo "Run 'make superset-image' and 'make superset-load' from a shell with Docker access." >&2
    exit 1
  fi

  echo "==> Building local Superset platform image..."
  SUPERSET_IMAGE_REPOSITORY="${SUPERSET_IMAGE_REPOSITORY}" \
    SUPERSET_IMAGE_TAG="${SUPERSET_IMAGE_TAG}" \
    bash "${SCRIPT_DIR}/../images/build-superset.sh"

  echo "==> Ensuring local Superset platform image is available to kind..."
  SUPERSET_IMAGE_REPOSITORY="${SUPERSET_IMAGE_REPOSITORY}" \
    SUPERSET_IMAGE_TAG="${SUPERSET_IMAGE_TAG}" \
    bash "${SCRIPT_DIR}/../images/load-superset.sh"
}

terraform_apply_once() {
  cleanup_failed_jobs_by_prefix "polaris-bootstrap-"

  terraform -chdir="${TERRAFORM_DIR}" apply -auto-approve \
    -var="namespace=${NAMESPACE}" \
    -var="kube_context=${KUBE_CONTEXT}" \
    -var="kubeconfig_path=${KUBECONFIG_PATH}" \
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

state_has_resource() {
  local resource_addr="$1"
  terraform -chdir="${TERRAFORM_DIR}" state show "${resource_addr}" >/dev/null 2>&1
}

reset_drifted_local_platform_if_needed() {
  if ! kubectl --context "${KUBE_CONTEXT}" get namespace "${NAMESPACE}" >/dev/null 2>&1; then
    return 0
  fi

  if state_has_resource "module.seaweedfs.helm_release.seaweedfs"; then
    return 0
  fi

  echo "WARN: local platform resources exist in-cluster but Terraform state is missing core objects." >&2
  echo "WARN: resetting the local platform before apply to recover from state drift..." >&2
  NAMESPACE="${NAMESPACE}" \
  CLUSTER_NAME="${CLUSTER_NAME}" \
  KUBE_CONTEXT="${KUBE_CONTEXT}" \
  FOUNDATION_STATE_PATH="${FOUNDATION_STATE_PATH}" \
    bash "${SCRIPT_DIR}/teardown.sh"

  echo "==> Reinitializing Terraform after local platform reset..."
  terraform -chdir="${TERRAFORM_DIR}" init
}

echo "==> Checking static platform prerequisites..."
check_prereqs terraform kubectl helm uv base64
check_cluster
require_kube_context

prepare_local_superset_image
prepare_helm_cache_dirs
prepare_cached_chart "Trino" trino "${TRINO_CHART_REPOSITORY}" trino/trino \
  "${TRINO_CHART_VERSION}" "${TRINO_CHART_PACKAGE_PATH}"

echo "==> Initializing Terraform..."
terraform -chdir="${TERRAFORM_DIR}" init
reset_drifted_local_platform_if_needed
terraform_import_namespace_args=(
  -var="namespace=${NAMESPACE}"
  -var="kube_context=${KUBE_CONTEXT}"
  -var="kubeconfig_path=${KUBECONFIG_PATH}"
  -var="foundation_state_path=${FOUNDATION_STATE_PATH}"
  -var="project_code_image_repository=${PROJECT_CODE_IMAGE_REPOSITORY}"
  -var="project_code_image_tag=${PROJECT_CODE_IMAGE_TAG}"
  -var="project_code_image_pull_policy=${PROJECT_CODE_IMAGE_PULL_POLICY}"
  -var="project_code_image_revision=${PROJECT_CODE_IMAGE_REVISION}"
  -var="superset_image_repository=${SUPERSET_IMAGE_REPOSITORY}"
  -var="superset_image_tag=${SUPERSET_IMAGE_TAG}"
  -var="superset_image_pull_policy=${SUPERSET_IMAGE_PULL_POLICY}"
  -var="trino_chart_package_path=${TRINO_CHART_PACKAGE_PATH}"
)
import_namespace_if_missing_in_state \
  "${TERRAFORM_DIR}" \
  "kubernetes_namespace_v1.lakehouse" \
  "${NAMESPACE}" \
  "${terraform_import_namespace_args[@]}"

echo "==> Applying Terraform local platform..."
run_with_retry "Terraform apply" terraform_apply_once

echo "Static OpenLakeForge local platform is applied."
