#!/usr/bin/env bash
# Pre-pull heavy images into kind nodes so Helm releases don't time out on first deploy.
# Run this once after creating the cluster: make local-prefetch
set -euo pipefail

CLUSTER_NAME="${CLUSTER_NAME:-openlakeforge-local}"

IMAGES=(
  "opensearchproject/opensearch:2.11.0"
  "docker.getcollate.io/openmetadata/server:1.12.10"
  "docker.getcollate.io/openmetadata/ingestion-base:1.12.10"
  "postgres:16-alpine"
  "apache/superset:dockerize"
  "apache/superset:6.1.0"
  "docker.io/bitnamilegacy/redis:7.0.10-debian-11-r4"
  "ghcr.io/malon64/floe:0.6.3"
)

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "${WORK_DIR}"' EXIT

nodes="$(kind get nodes --name "${CLUSTER_NAME}")"

if [[ -z "${nodes}" ]]; then
  echo "ERROR: kind cluster '${CLUSTER_NAME}' has no nodes." >&2
  exit 1
fi

for image in "${IMAGES[@]}"; do
  echo "==> Pulling $image..."
  docker pull "$image"

  archive="${WORK_DIR}/$(echo "${image}" | tr '/:@' '____').tar"
  echo "==> Saving $image..."
  docker save "$image" -o "${archive}"

  for node in ${nodes}; do
    echo "==> Loading $image into kind node '${node}'..."
    docker exec --privileged -i "${node}" \
      ctr --namespace=k8s.io images import --digests --snapshotter=overlayfs - <"${archive}"
  done
done

echo "All images pre-loaded."
