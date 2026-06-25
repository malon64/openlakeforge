#!/usr/bin/env bash
# Tear down the OpenLakeForge AWS POC platform. EKS and ECR are left intact.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
TERRAFORM_DIR="${REPO_ROOT}/infra/terraform/environments/aws-poc"
FOUNDATION_TERRAFORM_DIR="${REPO_ROOT}/infra/terraform/foundations/aws-eks"
FOUNDATION_STATE_PATH="${FOUNDATION_STATE_PATH:-${FOUNDATION_TERRAFORM_DIR}/terraform.tfstate}"
NAMESPACE="${NAMESPACE:-lakehouse}"
AWS_CLUSTER_NAME="${AWS_CLUSTER_NAME:-eks-openlakeforge-poc}"
KUBE_CONTEXT="${KUBE_CONTEXT:-}"

check_prereqs() {
  local missing=0
  for cmd in aws kubectl terraform; do
    if ! command -v "${cmd}" &>/dev/null; then
      echo "ERROR: '${cmd}' not found on PATH" >&2
      missing=1
    fi
  done
  [[ "${missing}" -eq 0 ]] || exit 1
}

echo "==> Checking AWS teardown prerequisites..."
check_prereqs

if [[ ! -f "${FOUNDATION_STATE_PATH}" ]]; then
  echo "ERROR: AWS foundation Terraform state is missing: ${FOUNDATION_STATE_PATH}" >&2
  exit 1
fi

AWS_REGION="$(terraform -chdir="${FOUNDATION_TERRAFORM_DIR}" output -raw aws_region)"
AWS_CLUSTER_NAME="$(terraform -chdir="${FOUNDATION_TERRAFORM_DIR}" output -raw cluster_name)"
KUBE_CONTEXT="${KUBE_CONTEXT:-${AWS_CLUSTER_NAME}}"

aws eks update-kubeconfig \
  --region "${AWS_REGION}" \
  --name "${AWS_CLUSTER_NAME}" \
  --alias "${KUBE_CONTEXT}" >/dev/null

if ! kubectl cluster-info --context "${KUBE_CONTEXT}" >/dev/null 2>&1; then
  echo "ERROR: Kubernetes context '${KUBE_CONTEXT}' is not reachable." >&2
  exit 1
fi
kubectl config use-context "${KUBE_CONTEXT}" >/dev/null

echo "==> Removing completed Superset init hook job if present..."
kubectl --context "${KUBE_CONTEXT}" delete job superset-init-db -n "${NAMESPACE}" \
  --ignore-not-found \
  --wait=true

echo "==> Destroying Terraform AWS POC stack..."
terraform -chdir="${TERRAFORM_DIR}" init
terraform -chdir="${TERRAFORM_DIR}" destroy \
  -auto-approve \
  -var="namespace=${NAMESPACE}" \
  -var="aws_region=${AWS_REGION}" \
  -var="kube_context=${KUBE_CONTEXT}" \
  -var="foundation_state_path=${FOUNDATION_STATE_PATH}"

echo "==> Deleting namespace '${NAMESPACE}' if it still exists..."
kubectl --context "${KUBE_CONTEXT}" delete namespace "${NAMESPACE}" --ignore-not-found

echo "AWS POC platform teardown complete. EKS and ECR are still running."
echo "To delete the AWS foundation: make aws-foundation-down"
