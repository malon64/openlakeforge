#!/usr/bin/env bash
# Apply the static OpenLakeForge local infrastructure.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
TERRAFORM_DIR="${REPO_ROOT}/infra/terraform/environments/local"
FOUNDATION_TERRAFORM_DIR="${REPO_ROOT}/infra/terraform/foundations/local-kind"
FOUNDATION_STATE_PATH="${FOUNDATION_STATE_PATH:-${FOUNDATION_TERRAFORM_DIR}/terraform.tfstate}"
NAMESPACE="${NAMESPACE:-lakehouse}"
CLUSTER_NAME="${CLUSTER_NAME:-openlakeforge-local}"
KUBE_CONTEXT="${KUBE_CONTEXT:-kind-${CLUSTER_NAME}}"
PROJECT_CODE_IMAGE_REPOSITORY="${PROJECT_CODE_IMAGE_REPOSITORY:-ghcr.io/openlakeforge/project-code}"
PROJECT_CODE_IMAGE_TAG="${PROJECT_CODE_IMAGE_TAG:-local}"
PROJECT_CODE_IMAGE_PULL_POLICY="${PROJECT_CODE_IMAGE_PULL_POLICY:-Never}"
PROJECT_CODE_IMAGE_REVISION="${PROJECT_CODE_IMAGE_REVISION:-manual}"
SUPERSET_IMAGE_REPOSITORY="${SUPERSET_IMAGE_REPOSITORY:-ghcr.io/openlakeforge/superset}"
SUPERSET_IMAGE_TAG="${SUPERSET_IMAGE_TAG:-local}"
SUPERSET_IMAGE_PULL_POLICY="${SUPERSET_IMAGE_PULL_POLICY:-Never}"
POLARIS_BOOTSTRAP_GENERATION="${POLARIS_BOOTSTRAP_GENERATION:-manual}"
POLARIS_LOCAL_PORT="${POLARIS_LOCAL_PORT:-18181}"
HELM_REPOSITORY_CONFIG="${HELM_REPOSITORY_CONFIG:-${REPO_ROOT}/.tmp/helm/repositories.yaml}"
HELM_REPOSITORY_CACHE="${HELM_REPOSITORY_CACHE:-${REPO_ROOT}/.tmp/helm/repository-cache}"
HELM_CHART_CACHE_DIR="${HELM_CHART_CACHE_DIR:-${REPO_ROOT}/.tmp/helm/charts}"
TRINO_CHART_REPOSITORY="${TRINO_CHART_REPOSITORY:-https://trinodb.github.io/charts}"
TRINO_CHART_VERSION="${TRINO_CHART_VERSION:-1.42.2}"
TRINO_CHART_PACKAGE_PATH="${TRINO_CHART_PACKAGE_PATH:-${HELM_CHART_CACHE_DIR}/trino-${TRINO_CHART_VERSION}.tgz}"

check_prereqs() {
  local missing=0
  for cmd in terraform kubectl helm curl base64; do
    if ! command -v "${cmd}" &>/dev/null; then
      echo "ERROR: '${cmd}' not found on PATH" >&2
      missing=1
    fi
  done
  [[ "${missing}" -eq 0 ]] || exit 1
}

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

run_with_retry() {
  local description="$1"
  shift

  local max_attempts="${LOCAL_UP_RETRY_ATTEMPTS:-4}"
  local delay_seconds="${LOCAL_UP_RETRY_DELAY_SECONDS:-20}"
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

prepare_local_helm_chart_cache() {
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

secret_value() {
  local secret_name="$1"
  local key="$2"

  kubectl get secret "${secret_name}" -n "${NAMESPACE}" \
    -o "jsonpath={.data.${key}}" | base64 -d
}

cleanup_failed_openmetadata_bootstrap_jobs() {
  local job

  while IFS= read -r job; do
    [[ -n "${job}" ]] || continue
    kubectl delete "${job}" -n "${NAMESPACE}" --ignore-not-found
  done < <(
    kubectl get jobs -n "${NAMESPACE}" -o name 2>/dev/null \
      | grep '^job.batch/openmetadata-bootstrap-' || true
  )
}

cleanup_failed_openmetadata_refresh_jobs() {
  local failed
  local job

  while IFS= read -r job; do
    [[ -n "${job}" ]] || continue
    failed="$(kubectl get "${job}" -n "${NAMESPACE}" -o jsonpath='{.status.failed}' 2>/dev/null || true)"
    if [[ -n "${failed}" && "${failed}" != "0" ]]; then
      kubectl delete "${job}" -n "${NAMESPACE}" --ignore-not-found
    fi
  done < <(
    kubectl get jobs -n "${NAMESPACE}" -o name 2>/dev/null \
      | grep '^job.batch/openmetadata-polaris-refresh-' || true
  )
}

cleanup_polaris_bootstrap_jobs() {
  local job

  while IFS= read -r job; do
    [[ -n "${job}" ]] || continue
    kubectl delete "${job}" -n "${NAMESPACE}" --ignore-not-found
  done < <(
    kubectl get jobs -n "${NAMESPACE}" -o name 2>/dev/null \
      | grep '^job.batch/polaris-bootstrap-' || true
  )
}

cleanup_failed_polaris_bootstrap_jobs() {
  local failed
  local job

  while IFS= read -r job; do
    [[ -n "${job}" ]] || continue
    failed="$(kubectl get "${job}" -n "${NAMESPACE}" -o jsonpath='{.status.failed}' 2>/dev/null || true)"
    if [[ -n "${failed}" && "${failed}" != "0" ]]; then
      kubectl delete "${job}" -n "${NAMESPACE}" --ignore-not-found
    fi
  done < <(
    kubectl get jobs -n "${NAMESPACE}" -o name 2>/dev/null \
      | grep '^job.batch/polaris-bootstrap-' || true
  )
}

prepare_polaris_bootstrap_generation() {
  local service="polaris"
  local secret_name="polaris-om-creds"
  local client_id
  local client_secret
  local status
  local port_forward_pid

  if ! kubectl get service "${service}" -n "${NAMESPACE}" >/dev/null 2>&1 ||
    ! kubectl get secret "${secret_name}" -n "${NAMESPACE}" >/dev/null 2>&1; then
    return 0
  fi

  client_id="$(secret_value "${secret_name}" "POLARIS_OM_CLIENT_ID")"
  client_secret="$(secret_value "${secret_name}" "POLARIS_OM_CLIENT_SECRET")"

  kubectl port-forward "svc/${service}" "${POLARIS_LOCAL_PORT}:8181" -n "${NAMESPACE}" \
    >/tmp/openlakeforge-polaris-port-forward.log 2>&1 &
  port_forward_pid="$!"

  for _ in $(seq 1 30); do
    if curl -fsS --max-time 2 "http://127.0.0.1:${POLARIS_LOCAL_PORT}/q/health" >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done

  status="$(curl -sS --max-time 10 -o /tmp/openlakeforge-polaris-token-check-body -w '%{http_code}' \
    -X POST "http://127.0.0.1:${POLARIS_LOCAL_PORT}/api/catalog/v1/oauth/tokens" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    --data-urlencode "grant_type=client_credentials" \
    --data-urlencode "client_id=${client_id}" \
    --data-urlencode "client_secret=${client_secret}" \
    --data-urlencode "scope=PRINCIPAL_ROLE:ALL" || true)"

  kill "${port_forward_pid}" >/dev/null 2>&1 || true

  case "${status}" in
    200)
      return 0
      ;;
    401)
      echo "WARN: Polaris service-principal credentials are stale; forcing Polaris bootstrap." >&2
      POLARIS_BOOTSTRAP_GENERATION="rebootstrap-$(date -u +%Y%m%d%H%M%S)"
      cleanup_polaris_bootstrap_jobs
      cleanup_failed_openmetadata_bootstrap_jobs
      cleanup_failed_openmetadata_refresh_jobs
      ;;
    *)
      echo "WARN: Polaris credential preflight returned HTTP ${status}; leaving bootstrap generation unchanged." >&2
      cat /tmp/openlakeforge-polaris-token-check-body >&2 || true
      ;;
  esac
}

terraform_apply_once() {
  cleanup_failed_polaris_bootstrap_jobs

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

terraform_apply_with_retry() {
  run_with_retry "Terraform apply" \
    terraform_apply_once
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
check_prereqs
check_cluster
kubectl config use-context "${KUBE_CONTEXT}" >/dev/null

prepare_local_superset_image
prepare_local_helm_chart_cache
prepare_polaris_bootstrap_generation

echo "==> Initializing Terraform..."
terraform -chdir="${TERRAFORM_DIR}" init

echo "==> Applying Terraform local infrastructure..."
terraform_apply_with_retry
refresh_trino_if_catalog_credentials_are_stale

echo "Static OpenLakeForge local infrastructure is applied."
