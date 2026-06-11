#!/usr/bin/env bash
# Delete the local kind cluster for OpenLakeForge development.
set -euo pipefail

CLUSTER_NAME="${CLUSTER_NAME:-openlakeforge-local}"

echo "==> Checking prerequisites..."
for cmd in kind; do
  if ! command -v "${cmd}" &>/dev/null; then
    echo "ERROR: '${cmd}' not found. Install it and retry." >&2
    echo "  kind: https://kind.sigs.k8s.io/docs/user/quick-start/#installation" >&2
    exit 1
  fi
done

if kind get clusters 2>/dev/null | grep -qx "${CLUSTER_NAME}"; then
  echo "==> Deleting kind cluster '${CLUSTER_NAME}'..."
  kind delete cluster --name "${CLUSTER_NAME}"
else
  echo "Cluster '${CLUSTER_NAME}' does not exist."
fi
