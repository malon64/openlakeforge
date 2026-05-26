#!/usr/bin/env bash
# Load the local project-code image into the kind cluster.
set -euo pipefail

CLUSTER_NAME="${CLUSTER_NAME:-openlakeforge-local}"
IMAGE_REPOSITORY="${PROJECT_CODE_IMAGE_REPOSITORY:-ghcr.io/openlakeforge/project-code}"
IMAGE_TAG="${PROJECT_CODE_IMAGE_TAG:-local}"
IMAGE="${PROJECT_CODE_IMAGE:-${IMAGE_REPOSITORY}:${IMAGE_TAG}}"

for cmd in docker; do
  if ! command -v "${cmd}" &>/dev/null; then
    echo "ERROR: '${cmd}' not found on PATH" >&2
    exit 1
  fi
done

if ! docker image inspect "${IMAGE}" >/dev/null 2>&1; then
  echo "ERROR: local image '${IMAGE}' does not exist." >&2
  echo "Run 'make project-code-image' first." >&2
  exit 1
fi

if command -v kind &>/dev/null; then
  if ! kind get clusters 2>/dev/null | grep -qx "${CLUSTER_NAME}"; then
    echo "ERROR: kind cluster '${CLUSTER_NAME}' does not exist." >&2
    echo "Run 'make local-cluster' first." >&2
    exit 1
  fi

  echo "==> Loading ${IMAGE} into kind cluster '${CLUSTER_NAME}'"
  kind load docker-image "${IMAGE}" --name "${CLUSTER_NAME}"
else
  node_prefix="${CLUSTER_NAME}-"
  mapfile -t nodes < <(docker ps --format '{{.Names}}' | grep -E "^${node_prefix}(control-plane|worker)" || true)

  if [[ "${#nodes[@]}" -eq 0 ]]; then
    echo "ERROR: kind is not installed and no Docker node containers were found for '${CLUSTER_NAME}'." >&2
    echo "Install kind or run 'make local-cluster' first." >&2
    exit 1
  fi

  for node in "${nodes[@]}"; do
    echo "==> Importing ${IMAGE} into kind node '${node}'"
    docker save "${IMAGE}" | docker exec -i "${node}" ctr -n k8s.io images import -
  done
fi

echo "Loaded ${IMAGE}"
