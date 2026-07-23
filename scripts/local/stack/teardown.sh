#!/usr/bin/env bash
# Tear down the OpenLakeForge local stack. The kind cluster itself is left
# intact.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
TERRAFORM_DIR="${REPO_ROOT}/infra/terraform/environments/local"
FOUNDATION_TERRAFORM_DIR="${REPO_ROOT}/infra/terraform/foundations/local-kind"
FOUNDATION_STATE_PATH="${FOUNDATION_STATE_PATH:-${FOUNDATION_TERRAFORM_DIR}/terraform.tfstate}"
NAMESPACE="${NAMESPACE:-lakehouse}"
CLUSTER_NAME="${CLUSTER_NAME:-openlakeforge-local}"
KUBE_CONTEXT="${KUBE_CONTEXT:-kind-${CLUSTER_NAME}}"
KUBECONFIG_PATH="${KUBECONFIG_PATH:-${REPO_ROOT}/.tmp/kubeconfigs/local.yaml}"
export KUBECONFIG="${KUBECONFIG_PATH}"

check_prereqs() {
  local missing=0
  for cmd in terraform kubectl; do
    if ! command -v "${cmd}" &>/dev/null; then
      echo "ERROR: '${cmd}' not found on PATH" >&2
      missing=1
    fi
  done
  [[ $missing -eq 0 ]] || exit 1
}

echo "==> Checking prerequisites..."
check_prereqs

if ! kubectl cluster-info --context "${KUBE_CONTEXT}" >/dev/null 2>&1; then
  echo "ERROR: Kubernetes context '${KUBE_CONTEXT}' is not reachable." >&2
  echo "The platform must be destroyed before the local foundation is destroyed." >&2
  exit 1
fi
if [[ ! -f "${FOUNDATION_STATE_PATH}" ]]; then
  echo "ERROR: local foundation Terraform state is missing: ${FOUNDATION_STATE_PATH}" >&2
  echo "The platform state depends on the foundation contract; restore or recreate it before running local-platform-down." >&2
  exit 1
fi
echo "==> Removing completed Superset init hook job if present..."
kubectl --context "${KUBE_CONTEXT}" delete job superset-init-db -n "${NAMESPACE}" \
  --ignore-not-found \
  --wait=true

echo "==> Destroying Terraform local stack..."
terraform -chdir="${TERRAFORM_DIR}" init
terraform -chdir="${TERRAFORM_DIR}" destroy \
  -auto-approve \
  -var="namespace=${NAMESPACE}" \
  -var="kube_context=${KUBE_CONTEXT}" \
  -var="kubeconfig_path=${KUBECONFIG_PATH}" \
  -var="foundation_state_path=${FOUNDATION_STATE_PATH}"

# One-time compatibility cleanup for stacks that were deployed before Terraform
# owned the local environment.
if command -v helm &>/dev/null; then
  echo "==> Removing legacy unmanaged Helm releases if present..."
  for release in trino polaris seaweedfs garage; do
    if helm --kube-context "${KUBE_CONTEXT}" status "${release}" -n "${NAMESPACE}" &>/dev/null; then
      helm --kube-context "${KUBE_CONTEXT}" uninstall "${release}" -n "${NAMESPACE}"
    fi
  done
fi

echo "==> Deleting namespace '${NAMESPACE}' if it still exists..."
kubectl --context "${KUBE_CONTEXT}" delete namespace "${NAMESPACE}" --ignore-not-found

echo "Teardown complete. Kind cluster is still running."
echo "To delete the cluster foundation: make local-foundation-down"
