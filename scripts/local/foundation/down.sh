#!/usr/bin/env bash
# Destroy the local cluster foundation Terraform root.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
TERRAFORM_DIR="${REPO_ROOT}/infra/terraform/foundations/local-kind"
NAMESPACE="${NAMESPACE:-lakehouse}"
CLUSTER_NAME="${CLUSTER_NAME:-openlakeforge-local}"
CLUSTER_CONFIG="${CLUSTER_CONFIG:-${REPO_ROOT}/infra/kind/local/kind-cluster.yaml}"
KUBE_CONTEXT="${KUBE_CONTEXT:-kind-${CLUSTER_NAME}}"
FORCE_DOWN="${LOCAL_FOUNDATION_FORCE_DOWN:-false}"

check_prereqs() {
  local missing=0
  for cmd in terraform kind kubectl; do
    if ! command -v "${cmd}" &>/dev/null; then
      echo "ERROR: '${cmd}' not found on PATH" >&2
      missing=1
    fi
  done
  [[ "${missing}" -eq 0 ]] || exit 1
}

echo "==> Checking local foundation prerequisites..."
check_prereqs

echo "==> Initializing Terraform local kind foundation..."
terraform -chdir="${TERRAFORM_DIR}" init

if ! terraform -chdir="${TERRAFORM_DIR}" state list 2>/dev/null | grep -qx "terraform_data.kind_cluster"; then
  if kind get clusters 2>/dev/null | grep -qx "${CLUSTER_NAME}"; then
    echo "ERROR: kind cluster '${CLUSTER_NAME}' exists, but the local foundation Terraform state does not own it." >&2
    echo "Run 'make local-foundation-up' first to adopt the existing cluster into the foundation state." >&2
    exit 1
  fi

  echo "No local foundation state or kind cluster exists for '${CLUSTER_NAME}'."
  exit 0
fi

if [[ "${FORCE_DOWN}" != "true" ]] &&
  kind get clusters 2>/dev/null | grep -qx "${CLUSTER_NAME}" &&
  kubectl --context "${KUBE_CONTEXT}" get namespace "${NAMESPACE}" >/dev/null 2>&1; then
  echo "ERROR: namespace '${NAMESPACE}' still exists on '${KUBE_CONTEXT}'." >&2
  echo "Run 'make local-down' before destroying the local foundation." >&2
  echo "Set LOCAL_FOUNDATION_FORCE_DOWN=true only if you intentionally want to delete the cluster with platform resources still present." >&2
  exit 1
fi

echo "==> Destroying Terraform local kind foundation..."
terraform -chdir="${TERRAFORM_DIR}" destroy -auto-approve \
  -var="cluster_name=${CLUSTER_NAME}" \
  -var="cluster_config_path=${CLUSTER_CONFIG}"

echo "Local foundation is destroyed."
