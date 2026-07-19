#!/usr/bin/env bash
# Build and push the OpenLakeForge Superset image to Azure Container Registry.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
FOUNDATION_TERRAFORM_DIR="${REPO_ROOT}/infra/terraform/foundations/azure-aks"
source "${REPO_ROOT}/scripts/lib/docker.sh"

git_or_time_tag() {
  git -C "${REPO_ROOT}" rev-parse --short HEAD 2>/dev/null || date -u +%Y%m%d%H%M%S
}

for cmd in az docker terraform; do
  if ! command -v "${cmd}" &>/dev/null; then
    echo "ERROR: '${cmd}' not found on PATH" >&2
    exit 1
  fi
done

ACR_LOGIN_SERVER="${ACR_LOGIN_SERVER:-$(terraform -chdir="${FOUNDATION_TERRAFORM_DIR}" output -raw acr_login_server 2>/dev/null || true)}"
if [[ -z "${ACR_LOGIN_SERVER}" ]]; then
  echo "ERROR: ACR_LOGIN_SERVER is unset and could not be read from the Azure foundation state." >&2
  exit 1
fi

ACR_NAME="${ACR_NAME:-$(terraform -chdir="${FOUNDATION_TERRAFORM_DIR}" output -raw acr_name 2>/dev/null || true)}"
ACR_NAME="${ACR_NAME:-${ACR_LOGIN_SERVER%%.*}}"
AZURE_IMAGE_TAG="${AZURE_IMAGE_TAG:-azure-$(git_or_time_tag)}"
AZURE_IMAGE_PLATFORM="${AZURE_IMAGE_PLATFORM:-linux/amd64}"
SUPERSET_IMAGE_REPOSITORY="${SUPERSET_IMAGE_REPOSITORY:-${ACR_LOGIN_SERVER}/openlakeforge/superset}"
SUPERSET_IMAGE_TAG="${SUPERSET_IMAGE_TAG:-${AZURE_IMAGE_TAG}}"
SUPERSET_BASE_IMAGE="${SUPERSET_BASE_IMAGE:-apache/superset:6.1.0@sha256:fb3464528ec7076f91195f0ff7835755aa023e281f1bb78a84782ce7a36b3705}"
IMAGE="${SUPERSET_IMAGE_REPOSITORY}:${SUPERSET_IMAGE_TAG}"

echo "==> Logging in to ACR '${ACR_NAME}'..."
az acr login --name "${ACR_NAME}" >/dev/null

echo "==> Pulling Superset base image: ${SUPERSET_BASE_IMAGE}"
docker_pull_with_retries --platform "${AZURE_IMAGE_PLATFORM}" "${SUPERSET_BASE_IMAGE}"

echo "==> Building Superset image ${IMAGE}..."
docker_build_with_retries \
  --platform "${AZURE_IMAGE_PLATFORM}" \
  --build-arg "SUPERSET_BASE_IMAGE=${SUPERSET_BASE_IMAGE}" \
  --file "${REPO_ROOT}/images/superset/Dockerfile" \
  --tag "${IMAGE}" \
  "${REPO_ROOT}/images/superset"

echo "==> Pushing Superset image ${IMAGE}..."
docker_push_with_retries "${IMAGE}"

echo "Pushed ${IMAGE}"
