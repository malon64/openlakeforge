#!/usr/bin/env bash
# Build and push the OpenLakeForge project-code image to Azure Container Registry.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
FOUNDATION_TERRAFORM_DIR="${REPO_ROOT}/infra/terraform/foundations/azure-aks"

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
PROJECT_CODE_IMAGE_REPOSITORY="${PROJECT_CODE_IMAGE_REPOSITORY:-${ACR_LOGIN_SERVER}/openlakeforge/project-code}"
PROJECT_CODE_IMAGE_TAG="${PROJECT_CODE_IMAGE_TAG:-${AZURE_IMAGE_TAG}}"
IMAGE="${PROJECT_CODE_IMAGE_REPOSITORY}:${PROJECT_CODE_IMAGE_TAG}"

echo "==> Logging in to ACR '${ACR_NAME}'..."
az acr login --name "${ACR_NAME}" >/dev/null

echo "==> Building project-code image ${IMAGE}..."
docker build \
  --platform "${AZURE_IMAGE_PLATFORM}" \
  --file "${REPO_ROOT}/images/project-code/Dockerfile" \
  --tag "${IMAGE}" \
  "${REPO_ROOT}"

echo "==> Pushing project-code image ${IMAGE}..."
docker push "${IMAGE}"

echo "Pushed ${IMAGE}"
