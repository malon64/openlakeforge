#!/usr/bin/env bash
# Pre-pull heavy images into kind nodes so Helm releases don't time out on first deploy.
# Run this once after creating the cluster: make local-prefetch
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
CLUSTER_NAME="${CLUSTER_NAME:-openlakeforge-local}"
DOCKER_PULL_ATTEMPTS="${LOCAL_PREFETCH_PULL_ATTEMPTS:-${DOCKER_PULL_ATTEMPTS:-5}}"
DOCKER_PULL_RETRY_DELAY_SECONDS="${LOCAL_PREFETCH_PULL_RETRY_DELAY_SECONDS:-${DOCKER_PULL_RETRY_DELAY_SECONDS:-20}}"

# shellcheck source=scripts/lib/docker.sh
source "${REPO_ROOT}/scripts/lib/docker.sh"

DOCKER_SERVER_ARCH="$(docker version --format '{{.Server.Arch}}' 2>/dev/null || uname -m)"
case "${DOCKER_SERVER_ARCH}" in
  arm64|aarch64)
    POLARIS_IMAGE="apache/polaris:1.4.0@sha256:705f7c0294bee6e8f1586276205d5525a714ed134549ab6f945850b6ee85fc94"
    ;;
  amd64|x86_64)
    POLARIS_IMAGE="apache/polaris:1.4.0@sha256:f4676e56a3a64bfa742c8ace36e6eb9f28697e4a6c2eca46b4869e094edb4d41"
    ;;
  *)
    POLARIS_IMAGE="apache/polaris:1.4.0"
    ;;
esac

IMAGES=(
  "opensearchproject/opensearch:2.11.0"
  "${POLARIS_IMAGE}"
  "docker.getcollate.io/openmetadata/server:1.12.10"
  "docker.getcollate.io/openmetadata/ingestion-base:1.12.10"
  "postgres:16-alpine"
  "apache/superset:dockerize"
  "apache/superset:6.1.0"
  "docker.io/bitnamilegacy/redis:7.0.10-debian-11-r4"
  "ghcr.io/malon64/floe:0.6.8"
)

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "${WORK_DIR}"' EXIT

nodes="$(kind get nodes --name "${CLUSTER_NAME}")"

if [[ -z "${nodes}" ]]; then
  echo "ERROR: kind cluster '${CLUSTER_NAME}' has no nodes." >&2
  exit 1
fi

for image in "${IMAGES[@]}"; do
  if docker image inspect "$image" >/dev/null 2>&1; then
    echo "==> Using existing local image $image..."
  else
    echo "==> Pulling $image..."
    docker_pull_with_retries "$image"
  fi

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
