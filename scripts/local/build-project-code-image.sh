#!/usr/bin/env bash
# Build the local OpenLakeForge project-code image.
set -euo pipefail

IMAGE_REPOSITORY="${PROJECT_CODE_IMAGE_REPOSITORY:-ghcr.io/openlakeforge/project-code}"
IMAGE_TAG="${PROJECT_CODE_IMAGE_TAG:-local}"
IMAGE="${PROJECT_CODE_IMAGE:-${IMAGE_REPOSITORY}:${IMAGE_TAG}}"

if ! command -v docker &>/dev/null; then
  echo "ERROR: 'docker' not found on PATH" >&2
  exit 1
fi

if [[ "${SKIP_FLOE_MANIFEST:-0}" != "1" ]]; then
  bash scripts/local/floe-manifest.sh
fi

echo "==> Building project-code image: ${IMAGE}"
docker build \
  --file images/project-code/Dockerfile \
  --tag "${IMAGE}" \
  .

echo "Built ${IMAGE}"
