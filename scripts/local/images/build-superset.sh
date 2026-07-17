#!/usr/bin/env bash
# Build the local OpenLakeForge Superset image with required database drivers.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
source "${REPO_ROOT}/scripts/lib/docker.sh"

IMAGE_REPOSITORY="${SUPERSET_IMAGE_REPOSITORY:-ghcr.io/openlakeforge/superset}"
IMAGE_TAG="${SUPERSET_IMAGE_TAG:-local}"
IMAGE="${SUPERSET_IMAGE:-${IMAGE_REPOSITORY}:${IMAGE_TAG}}"
SUPERSET_BASE_IMAGE="${SUPERSET_BASE_IMAGE:-apache/superset:6.1.0@sha256:fb3464528ec7076f91195f0ff7835755aa023e281f1bb78a84782ce7a36b3705}"

if ! command -v docker &>/dev/null; then
  echo "ERROR: 'docker' not found on PATH" >&2
  exit 1
fi

echo "==> Pulling Superset base image: ${SUPERSET_BASE_IMAGE}"
docker_pull_with_retries "${SUPERSET_BASE_IMAGE}"

echo "==> Building Superset image: ${IMAGE}"
docker_build_with_retries \
  --build-arg "SUPERSET_BASE_IMAGE=${SUPERSET_BASE_IMAGE}" \
  --file images/superset/Dockerfile \
  --tag "${IMAGE}" \
  images/superset

echo "Built ${IMAGE}"
