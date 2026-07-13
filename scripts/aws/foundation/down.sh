#!/usr/bin/env bash
# Destroy the AWS EKS foundation Terraform root.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
TERRAFORM_DIR="${REPO_ROOT}/infra/terraform/foundations/aws-eks"
NAMESPACE="${NAMESPACE:-lakehouse}"
AWS_REGION="${AWS_REGION:-eu-west-1}"
AWS_CLUSTER_NAME="${AWS_CLUSTER_NAME:-eks-openlakeforge-poc}"
AWS_NODE_DESIRED_SIZE="${AWS_NODE_DESIRED_SIZE:-3}"
AWS_NODE_MIN_SIZE="${AWS_NODE_MIN_SIZE:-1}"
AWS_NODE_MAX_SIZE="${AWS_NODE_MAX_SIZE:-4}"
AWS_NODE_INSTANCE_TYPES="${AWS_NODE_INSTANCE_TYPES:-m7i.large}"
if [[ "${AWS_NODE_INSTANCE_TYPES}" == \[* ]]; then
  NODE_INSTANCE_TYPES_VAR="${AWS_NODE_INSTANCE_TYPES}"
else
  NODE_INSTANCE_TYPES_VAR="[\"${AWS_NODE_INSTANCE_TYPES}\"]"
fi
AWS_FOUNDATION_FORCE_DOWN="${AWS_FOUNDATION_FORCE_DOWN:-false}"

# cluster_name comes from AWS_CLUSTER_NAME (passed explicitly to destroy below) and
# must match the value up.sh applied so destroy targets the same resource names.
# Mandatory tags live in a .tfvars file (override with AWS_TFVARS_FILE).
TFVARS_FILE="${AWS_TFVARS_FILE:-${TERRAFORM_DIR}/sandbox.tfvars}"
TFVARS_ARGS=()
[[ -f "${TFVARS_FILE}" ]] && TFVARS_ARGS+=(-var-file="${TFVARS_FILE}")

check_prereqs() {
  local missing=0
  for cmd in aws terraform kubectl; do
    if ! command -v "${cmd}" &>/dev/null; then
      echo "ERROR: '${cmd}' not found on PATH" >&2
      missing=1
    fi
  done
  [[ "${missing}" -eq 0 ]] || exit 1
}

echo "==> Checking AWS foundation prerequisites..."
check_prereqs

echo "==> Initializing Terraform AWS EKS foundation..."
terraform -chdir="${TERRAFORM_DIR}" init

if ! terraform -chdir="${TERRAFORM_DIR}" state list 2>/dev/null | grep -qx "aws_eks_cluster.this"; then
  echo "No AWS EKS foundation state exists."
  exit 0
fi

cluster_name="$(terraform -chdir="${TERRAFORM_DIR}" output -raw cluster_name)"
region="$(terraform -chdir="${TERRAFORM_DIR}" output -raw aws_region)"

if aws eks describe-cluster --region "${region}" --name "${cluster_name}" >/dev/null 2>&1; then
  aws eks update-kubeconfig --region "${region}" --name "${cluster_name}" --alias "${cluster_name}" >/dev/null
fi

if [[ "${AWS_FOUNDATION_FORCE_DOWN}" != "true" ]] &&
  kubectl --context "${cluster_name}" get namespace "${NAMESPACE}" >/dev/null 2>&1; then
  echo "ERROR: namespace '${NAMESPACE}' still exists on '${cluster_name}'." >&2
  echo "Run 'make aws-platform-down' before destroying the AWS foundation." >&2
  echo "Set AWS_FOUNDATION_FORCE_DOWN=true only if you intentionally want to delete the foundation with platform resources still present." >&2
  exit 1
fi

echo "==> Destroying Terraform AWS EKS foundation..."
terraform -chdir="${TERRAFORM_DIR}" destroy -auto-approve \
  ${TFVARS_ARGS[@]+"${TFVARS_ARGS[@]}"} \
  -var="cluster_name=${AWS_CLUSTER_NAME}" \
  -var="aws_region=${AWS_REGION}" \
  -var="node_desired_size=${AWS_NODE_DESIRED_SIZE}" \
  -var="node_min_size=${AWS_NODE_MIN_SIZE}" \
  -var="node_max_size=${AWS_NODE_MAX_SIZE}" \
  -var="node_instance_types=${NODE_INSTANCE_TYPES_VAR}"

echo "AWS EKS foundation is destroyed."
