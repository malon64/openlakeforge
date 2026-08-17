#!/usr/bin/env bash
# Deliberately-broken fixture: calls the Phase-2 image-set command before
# reconciling catalog namespaces, and reintroduces a forbidden Dagster
# rollout-restart. Used to prove the shell-orchestration test actually
# catches ordering/regression breakage, not just that it passes today.
set -euo pipefail

REPO_ROOT="${OPENLAKEFORGE_REPO_ROOT:?OPENLAKEFORGE_REPO_ROOT must be set}"
NAMESPACE="${NAMESPACE:-lakehouse}"
KUBE_CONTEXT="${KUBE_CONTEXT:-kind-openlakeforge-local}"
export OPENLAKEFORGE_REPO_ROOT="${REPO_ROOT}"

# shellcheck source=/dev/null
source "${REPO_ROOT}/scripts/lib/common.sh"
# shellcheck source=/dev/null
source "${REPO_ROOT}/scripts/lib/python.sh"

require_cmd kubectl
require_cmd uv

echo "==> Pointing Dagster at project-code image (WRONG: before namespace sync)..."
olf_run k8s set-project-code-image --image "ghcr.io/openlakeforge/project-code:local"

echo "==> Reconciling Polaris namespaces from the domain descriptors (too late)..."
olf_run catalog sync-namespaces

echo "==> Restarting Dagster deployment directly (forbidden: k8s set-project-code-image already does this)..."
kubectl --context "${KUBE_CONTEXT}" rollout restart deployment/dagster-dagster-webserver -n "${NAMESPACE}"

echo "Dynamic OpenLakeForge local artifacts are deployed."
