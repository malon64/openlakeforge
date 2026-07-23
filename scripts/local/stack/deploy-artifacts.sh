#!/usr/bin/env bash
# Deploy dynamic local domain artifacts after the static platform exists.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
NAMESPACE="${NAMESPACE:-lakehouse}"
CLUSTER_NAME="${CLUSTER_NAME:-openlakeforge-local}"
KUBE_CONTEXT="${KUBE_CONTEXT:-kind-${CLUSTER_NAME}}"
KUBECONFIG_PATH="${KUBECONFIG_PATH:-${REPO_ROOT}/.tmp/kubeconfigs/local.yaml}"
DEPLOYMENT_SCOPE="${DEPLOYMENT_SCOPE:-local}"
PROJECT_CODE_IMAGE_REPOSITORY="${PROJECT_CODE_IMAGE_REPOSITORY:-ghcr.io/openlakeforge/project-code}"
PROJECT_CODE_IMAGE_TAG="${PROJECT_CODE_IMAGE_TAG:-local}"
PROJECT_CODE_IMAGE="${PROJECT_CODE_IMAGE_REPOSITORY}:${PROJECT_CODE_IMAGE_TAG}"
FLOE_RUNTIME_ARTIFACT_DIR="${FLOE_RUNTIME_ARTIFACT_DIR:-${REPO_ROOT}/.tmp/floe-runtime/local}"
export OPENLAKEFORGE_REPO_ROOT="${REPO_ROOT}"

# shellcheck source=scripts/lib/common.sh
source "${REPO_ROOT}/scripts/lib/common.sh"
configure_deployment_scope
# shellcheck source=scripts/lib/kube.sh
source "${REPO_ROOT}/scripts/lib/kube.sh"
# shellcheck source=scripts/lib/python.sh
source "${REPO_ROOT}/scripts/lib/python.sh"

cd "${REPO_ROOT}"

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

require_cmd kubectl
require_cmd uv

require_kube_context

# Load the provider contract environment for the olf artifact commands.
# shellcheck source=/dev/null
source "${REPO_ROOT}/scripts/contracts/load-runtime-env.sh"

echo "==> Generating local product Floe manifests for namespace '${NAMESPACE}'..."
export FLOE_RUNTIME_ARTIFACT_DIR
export FLOE_PERSIST_RUNTIME_ARTIFACTS="true"
NAMESPACE="${NAMESPACE}" bash "${REPO_ROOT}/scripts/artifacts/floe-manifest.sh"

prepare_local_project_code_image

echo "==> Publishing product Floe manifests to local ops bucket..."
olf_run artifacts upload-manifests --via port-forward --runtime-root "${FLOE_RUNTIME_ARTIFACT_DIR}"

echo "==> Deploying product Superset report assets..."
olf_run superset deploy-reports

echo "==> Deploying OpenMetadata governance metadata..."
OPENMETADATA_ALLOW_MISSING_ASSETS="${OPENMETADATA_ALLOW_MISSING_ASSETS:-true}" \
  olf_run openmetadata deploy-metadata

echo "==> Pointing Dagster at project-code image ${PROJECT_CODE_IMAGE}..."
olf_run k8s set-project-code-image --image "${PROJECT_CODE_IMAGE}"

echo "Dynamic OpenLakeForge local artifacts are deployed."
