#!/usr/bin/env bash
# Deploy dynamic local domain artifacts after the static infrastructure exists.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAMESPACE="${NAMESPACE:-lakehouse}"
CLUSTER_NAME="${CLUSTER_NAME:-openlakeforge-local}"
KUBE_CONTEXT="${KUBE_CONTEXT:-kind-${CLUSTER_NAME}}"
PROJECT_CODE_IMAGE_REPOSITORY="${PROJECT_CODE_IMAGE_REPOSITORY:-ghcr.io/openlakeforge/project-code}"
PROJECT_CODE_IMAGE_TAG="${PROJECT_CODE_IMAGE_TAG:-local}"

require_cmd() {
  local cmd="$1"
  if ! command -v "${cmd}" &>/dev/null; then
    echo "ERROR: '${cmd}' not found on PATH" >&2
    exit 1
  fi
}

prepare_local_project_code_image() {
  if [[ "${PROJECT_CODE_IMAGE_TAG}" != "local" ]]; then
    return 0
  fi

  require_cmd docker

  echo "==> Building local project-code image..."
  PROJECT_CODE_IMAGE_REPOSITORY="${PROJECT_CODE_IMAGE_REPOSITORY}" \
    PROJECT_CODE_IMAGE_TAG="${PROJECT_CODE_IMAGE_TAG}" \
    bash "${SCRIPT_DIR}/../images/build-project-code.sh"

  echo "==> Ensuring local project-code image is available to kind..."
  CLUSTER_NAME="${CLUSTER_NAME}" \
  PROJECT_CODE_IMAGE_REPOSITORY="${PROJECT_CODE_IMAGE_REPOSITORY}" \
    PROJECT_CODE_IMAGE_TAG="${PROJECT_CODE_IMAGE_TAG}" \
    bash "${SCRIPT_DIR}/../images/load-project-code.sh"
}

restart_if_exists() {
  local deployment="$1"

  if ! kubectl get deployment "${deployment}" -n "${NAMESPACE}" >/dev/null 2>&1; then
    return 0
  fi

  echo "==> Restarting ${deployment} after dynamic artifact deployment..."
  kubectl rollout restart "deployment/${deployment}" -n "${NAMESPACE}"
  kubectl rollout status "deployment/${deployment}" -n "${NAMESPACE}" --timeout=600s
}

restart_dagster_project_code_deployments() {
  restart_if_exists "dagster-dagster-webserver"
  restart_if_exists "dagster-dagster-daemon"
  restart_if_exists "dagster-webserver"
  restart_if_exists "dagster-daemon"

  while IFS= read -r deployment; do
    restart_if_exists "${deployment}"
  done < <(
    kubectl get deployments -n "${NAMESPACE}" -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' \
      | grep -E 'dagster-user-deployments-.+-dagster$' || true
  )
}

require_cmd kubectl
require_cmd python3

if ! kubectl cluster-info --context "${KUBE_CONTEXT}" >/dev/null 2>&1; then
  echo "ERROR: Kubernetes context '${KUBE_CONTEXT}' is not reachable." >&2
  echo "Run 'make local-foundation-up' before deploying local artifacts." >&2
  exit 1
fi
kubectl config use-context "${KUBE_CONTEXT}" >/dev/null

echo "==> Generating local product Floe manifests for namespace '${NAMESPACE}'..."
NAMESPACE="${NAMESPACE}" bash "${SCRIPT_DIR}/../artifacts/floe-manifest.sh"

prepare_local_project_code_image

echo "==> Publishing product Floe manifests to local ops bucket..."
NAMESPACE="${NAMESPACE}" bash "${SCRIPT_DIR}/../artifacts/upload-floe-manifest.sh"

echo "==> Deploying product Superset report assets..."
NAMESPACE="${NAMESPACE}" bash "${SCRIPT_DIR}/../artifacts/superset-reports-deploy.sh"

echo "==> Deploying OpenMetadata governance metadata..."
NAMESPACE="${NAMESPACE}" \
  OPENMETADATA_ALLOW_MISSING_ASSETS="${OPENMETADATA_ALLOW_MISSING_ASSETS:-true}" \
  bash "${SCRIPT_DIR}/../artifacts/openmetadata-metadata-deploy.sh"

restart_dagster_project_code_deployments

echo "Dynamic OpenLakeForge local artifacts are deployed."
