#!/usr/bin/env bash
# Build the local OpenLakeForge project-code image.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
source "${REPO_ROOT}/scripts/lib/docker.sh"

IMAGE_REPOSITORY="${PROJECT_CODE_IMAGE_REPOSITORY:-ghcr.io/openlakeforge/project-code}"
IMAGE_TAG="${PROJECT_CODE_IMAGE_TAG:-local}"
IMAGE="${PROJECT_CODE_IMAGE:-${IMAGE_REPOSITORY}:${IMAGE_TAG}}"
PROJECT_CODE_PYTHON_BASE_IMAGE="${PROJECT_CODE_PYTHON_BASE_IMAGE:-python:3.12-slim}"

if ! command -v docker &>/dev/null; then
  echo "ERROR: 'docker' not found on PATH" >&2
  exit 1
fi

echo "==> Pulling project-code Python base image: ${PROJECT_CODE_PYTHON_BASE_IMAGE}"
docker_pull_with_retries "${PROJECT_CODE_PYTHON_BASE_IMAGE}"

echo "==> Building project-code image: ${IMAGE}"
docker_build_with_retries \
  --build-arg "PYTHON_BASE_IMAGE=${PROJECT_CODE_PYTHON_BASE_IMAGE}" \
  --file images/project-code/Dockerfile \
  --tag "${IMAGE}" \
  .

echo "Built ${IMAGE}"
