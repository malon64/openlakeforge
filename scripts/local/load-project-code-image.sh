#!/usr/bin/env bash
# Load the local project-code image into the kind cluster.
set -euo pipefail

CLUSTER_NAME="${CLUSTER_NAME:-openlakeforge-local}"
IMAGE_REPOSITORY="${PROJECT_CODE_IMAGE_REPOSITORY:-ghcr.io/openlakeforge/project-code}"
IMAGE_TAG="${PROJECT_CODE_IMAGE_TAG:-local}"
IMAGE="${PROJECT_CODE_IMAGE:-${IMAGE_REPOSITORY}:${IMAGE_TAG}}"

for cmd in kind docker; do
  if ! command -v "${cmd}" &>/dev/null; then
    echo "ERROR: '${cmd}' not found on PATH" >&2
    exit 1
  fi
done

if ! kind get clusters 2>/dev/null | grep -qx "${CLUSTER_NAME}"; then
  echo "ERROR: kind cluster '${CLUSTER_NAME}' does not exist." >&2
  echo "Run 'make local-cluster' first." >&2
  exit 1
fi

echo "==> Loading ${IMAGE} into kind cluster '${CLUSTER_NAME}'"
kind load docker-image "${IMAGE}" --name "${CLUSTER_NAME}"

echo "Loaded ${IMAGE}"
