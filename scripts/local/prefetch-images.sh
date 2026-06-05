#!/usr/bin/env bash
# Pre-pull heavy images into kind nodes so Helm releases don't time out on first deploy.
# Run this once after creating the cluster: make local-prefetch
set -euo pipefail

CLUSTER_NAME="${CLUSTER_NAME:-openlakeforge-local}"

IMAGES=(
  "opensearchproject/opensearch:2.11.0"
  "docker.getcollate.io/openmetadata/ingestion-base:1.12.10"
  "postgres:16-alpine"
)

for image in "${IMAGES[@]}"; do
  echo "==> Pulling $image..."
  docker pull "$image"
  echo "==> Loading $image into kind cluster '${CLUSTER_NAME}'..."
  kind load docker-image "$image" --name "${CLUSTER_NAME}"
done

echo "All images pre-loaded."
