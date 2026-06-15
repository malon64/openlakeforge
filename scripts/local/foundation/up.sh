#!/usr/bin/env bash
# Apply the local cluster foundation Terraform root.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
TERRAFORM_DIR="${REPO_ROOT}/infra/terraform/foundations/local-kind"
CLUSTER_NAME="${CLUSTER_NAME:-openlakeforge-local}"
CLUSTER_CONFIG="${CLUSTER_CONFIG:-${REPO_ROOT}/infra/kind/local/kind-cluster.yaml}"
KIND_WAIT_TIMEOUT="${KIND_WAIT_TIMEOUT:-120s}"
RESET_EXISTING_CLUSTER="${LOCAL_FOUNDATION_RESET:-false}"

check_prereqs() {
  local missing=0
  for cmd in terraform docker kind kubectl; do
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

echo "==> Applying Terraform local kind foundation..."
terraform -chdir="${TERRAFORM_DIR}" apply -auto-approve \
  -var="cluster_name=${CLUSTER_NAME}" \
  -var="cluster_config_path=${CLUSTER_CONFIG}" \
  -var="kind_wait_timeout=${KIND_WAIT_TIMEOUT}" \
  -var="reset_existing_cluster=${RESET_EXISTING_CLUSTER}"

kubectl config use-context "kind-${CLUSTER_NAME}" >/dev/null

echo ""
echo "Local foundation is ready."
echo "Kubernetes context: kind-${CLUSTER_NAME}"
