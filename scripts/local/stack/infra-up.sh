#!/usr/bin/env bash
# Apply the static OpenLakeForge local infrastructure.
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
export OPENLAKEFORGE_REPO_ROOT="${REPO_ROOT}"
PROJECT_CODE_IMAGE_REPOSITORY="${PROJECT_CODE_IMAGE_REPOSITORY:-ghcr.io/openlakeforge/project-code}"
PROJECT_CODE_IMAGE_TAG="${PROJECT_CODE_IMAGE_TAG:-local}"
PROJECT_CODE_IMAGE_PULL_POLICY="${PROJECT_CODE_IMAGE_PULL_POLICY:-Never}"
PROJECT_CODE_IMAGE_REVISION="${PROJECT_CODE_IMAGE_REVISION:-manual}"
SUPERSET_IMAGE_REPOSITORY="${SUPERSET_IMAGE_REPOSITORY:-ghcr.io/openlakeforge/superset}"
SUPERSET_IMAGE_TAG="${SUPERSET_IMAGE_TAG:-local}"
SUPERSET_IMAGE_PULL_POLICY="${SUPERSET_IMAGE_PULL_POLICY:-Never}"
POLARIS_BOOTSTRAP_GENERATION="${POLARIS_BOOTSTRAP_GENERATION:-manual}"
TRINO_CHART_REPOSITORY="${TRINO_CHART_REPOSITORY:-https://trinodb.github.io/charts}"
TRINO_CHART_VERSION="${TRINO_CHART_VERSION:-1.42.2}"

RUN_RETRY_ATTEMPTS="${LOCAL_UP_RETRY_ATTEMPTS:-4}"
RUN_RETRY_DELAY_SECONDS="${LOCAL_UP_RETRY_DELAY_SECONDS:-20}"

# shellcheck source=scripts/lib/common.sh
source "${REPO_ROOT}/scripts/lib/common.sh"
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

  if ! kubectl cluster-info --context "${KUBE_CONTEXT}" >/dev/null 2>&1; then
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
    -var="foundation_state_path=${FOUNDATION_STATE_PATH}" \
    -var="project_code_image_repository=${PROJECT_CODE_IMAGE_REPOSITORY}" \
    -var="project_code_image_tag=${PROJECT_CODE_IMAGE_TAG}" \
    -var="project_code_image_pull_policy=${PROJECT_CODE_IMAGE_PULL_POLICY}" \
    -var="project_code_image_revision=${PROJECT_CODE_IMAGE_REVISION}" \
    -var="superset_image_repository=${SUPERSET_IMAGE_REPOSITORY}" \
    -var="superset_image_tag=${SUPERSET_IMAGE_TAG}" \
    -var="superset_image_pull_policy=${SUPERSET_IMAGE_PULL_POLICY}" \
    -var="polaris_bootstrap_generation=${POLARIS_BOOTSTRAP_GENERATION}" \
    -var="trino_chart_package_path=${TRINO_CHART_PACKAGE_PATH}"
}

refresh_trino_if_catalog_credentials_are_stale() {
  local deployment="trino-coordinator"
  local check_output

  if ! kubectl get deployment "${deployment}" -n "${NAMESPACE}" >/dev/null 2>&1; then
    return 0
  fi

  echo "==> Checking Trino Iceberg catalog credentials..."
  if check_output="$(kubectl exec "deployment/${deployment}" -n "${NAMESPACE}" -- \
    trino --server http://localhost:8080 --execute "SHOW SCHEMAS FROM iceberg" 2>&1)"; then
    return 0
  fi

  case "${check_output}" in
    *unauthorized_client*|*"Cannot obtain metadata"*|*"ICEBERG_CATALOG_ERROR"*)
      echo "WARN: Trino has stale Polaris catalog credentials; restarting ${deployment}..." >&2
      ;;
    *)
      echo "${check_output}" >&2
      echo "ERROR: Trino Iceberg catalog credential check failed." >&2
      return 1
      ;;
  esac

  kubectl rollout restart "deployment/${deployment}" -n "${NAMESPACE}"
  kubectl rollout status "deployment/${deployment}" -n "${NAMESPACE}" --timeout=300s

  run_with_retry "Trino Iceberg catalog credential check" \
    kubectl exec "deployment/${deployment}" -n "${NAMESPACE}" -- \
      trino --server http://localhost:8080 --execute "SHOW SCHEMAS FROM iceberg"
}

echo "==> Checking static infrastructure prerequisites..."
check_prereqs terraform kubectl helm uv base64
check_cluster
kubectl config use-context "${KUBE_CONTEXT}" >/dev/null

prepare_local_superset_image
prepare_helm_cache_dirs
prepare_cached_chart "Trino" trino "${TRINO_CHART_REPOSITORY}" trino/trino \
  "${TRINO_CHART_VERSION}" "${TRINO_CHART_PACKAGE_PATH}"
prepare_polaris_bootstrap_generation

echo "==> Initializing Terraform..."
terraform -chdir="${TERRAFORM_DIR}" init

echo "==> Applying Terraform local infrastructure..."
run_with_retry "Terraform apply" terraform_apply_once
refresh_trino_if_catalog_credentials_are_stale

echo "Static OpenLakeForge local infrastructure is applied."
