#!/usr/bin/env bash
# Create a local kind cluster for OpenLakeForge development.
# Run from WSL with kind, docker, and kubectl on the PATH.
#
# Usage:
#   bash scripts/local/create-cluster.sh [--reset]
#
#   --reset  Delete an existing cluster with the same name before creating.
set -euo pipefail

CLUSTER_NAME="openlakeforge-local"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLUSTER_CONFIG="${SCRIPT_DIR}/../../infra/kind/cluster.yaml"

RESET=false
for arg in "$@"; do
  [[ "${arg}" == "--reset" ]] && RESET=true
done

# ── Prerequisites ──────────────────────────────────────────────────────────
echo "==> Checking prerequisites..."
for cmd in docker kind kubectl; do
  if ! command -v "${cmd}" &>/dev/null; then
    echo "ERROR: '${cmd}' not found. Install it and retry." >&2
    echo "  docker: https://docs.docker.com/engine/install/"   >&2
    echo "  kind:   https://kind.sigs.k8s.io/docs/user/quick-start/#installation" >&2
    echo "  kubectl:https://kubernetes.io/docs/tasks/tools/install-kubectl-linux/" >&2
    exit 1
  fi
done

if ! docker info &>/dev/null; then
  echo "ERROR: Docker daemon is not running or not accessible." >&2
  echo "  Start Docker Desktop and enable WSL integration, or install Docker Engine in WSL." >&2
  exit 1
fi

# ── Cluster lifecycle ──────────────────────────────────────────────────────
if kind get clusters 2>/dev/null | grep -qx "${CLUSTER_NAME}"; then
  if [[ "${RESET}" == true ]]; then
    echo "==> Deleting existing cluster '${CLUSTER_NAME}'..."
    kind delete cluster --name "${CLUSTER_NAME}"
  else
    echo "Cluster '${CLUSTER_NAME}' already exists. Use --reset to recreate it."
    kubectl cluster-info --context "kind-${CLUSTER_NAME}"
    exit 0
  fi
fi

echo "==> Creating kind cluster '${CLUSTER_NAME}'..."
kind create cluster \
  --name "${CLUSTER_NAME}" \
  --config "${CLUSTER_CONFIG}" \
  --wait 120s

# ── Verify ─────────────────────────────────────────────────────────────────
echo ""
echo "==> Cluster ready:"
kubectl get nodes
echo ""
echo "Context set to: kind-${CLUSTER_NAME}"
echo "Run 'make local-up' to deploy the lakehouse stack."
