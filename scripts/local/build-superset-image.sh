#!/usr/bin/env bash
# Build the local OpenLakeForge Superset image with required database drivers.
set -euo pipefail

IMAGE_REPOSITORY="${SUPERSET_IMAGE_REPOSITORY:-ghcr.io/openlakeforge/superset}"
IMAGE_TAG="${SUPERSET_IMAGE_TAG:-local}"
IMAGE="${SUPERSET_IMAGE:-${IMAGE_REPOSITORY}:${IMAGE_TAG}}"

if ! command -v docker &>/dev/null; then
  echo "ERROR: 'docker' not found on PATH" >&2
  exit 1
fi

echo "==> Building Superset image: ${IMAGE}"
docker build \
  --file images/superset/Dockerfile \
  --tag "${IMAGE}" \
  .

echo "Built ${IMAGE}"
