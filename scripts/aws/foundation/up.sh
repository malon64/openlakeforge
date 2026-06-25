#!/usr/bin/env bash
# Apply the AWS EKS foundation Terraform root and populate kubeconfig.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
TERRAFORM_DIR="${REPO_ROOT}/infra/terraform/foundations/aws-eks"
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

# Account-specific config (cluster_name with its required limited- prefix, mandatory
# tags) lives in a .tfvars file rather than the module's variable defaults. Override
# with AWS_TFVARS_FILE; if the file is absent the module's neutral defaults apply.
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
aws sts get-caller-identity >/dev/null

echo "==> Initializing Terraform AWS EKS foundation..."
terraform -chdir="${TERRAFORM_DIR}" init

echo "==> Applying Terraform AWS EKS foundation..."
terraform -chdir="${TERRAFORM_DIR}" apply -auto-approve \
  ${TFVARS_ARGS[@]+"${TFVARS_ARGS[@]}"} \
  -var="aws_region=${AWS_REGION}" \
  -var="node_desired_size=${AWS_NODE_DESIRED_SIZE}" \
  -var="node_min_size=${AWS_NODE_MIN_SIZE}" \
  -var="node_max_size=${AWS_NODE_MAX_SIZE}" \
  -var="node_instance_types=${NODE_INSTANCE_TYPES_VAR}"

cluster_name="$(terraform -chdir="${TERRAFORM_DIR}" output -raw cluster_name)"
region="$(terraform -chdir="${TERRAFORM_DIR}" output -raw aws_region)"

echo "==> Fetching EKS kube credentials..."
aws eks update-kubeconfig \
  --region "${region}" \
  --name "${cluster_name}" \
  --alias "${cluster_name}" >/dev/null

kubectl config use-context "${cluster_name}" >/dev/null
kubectl cluster-info --context "${cluster_name}" >/dev/null

echo ""
echo "AWS EKS foundation is ready."
echo "Kubernetes context: ${cluster_name}"
echo "Project-code ECR: $(terraform -chdir="${TERRAFORM_DIR}" output -raw project_code_ecr_repository_url)"
echo "Superset ECR:     $(terraform -chdir="${TERRAFORM_DIR}" output -raw superset_ecr_repository_url)"
