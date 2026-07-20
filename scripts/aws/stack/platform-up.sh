#!/usr/bin/env bash
# Apply the static OpenLakeForge AWS POC platform on EKS.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
TERRAFORM_DIR="${REPO_ROOT}/infra/terraform/environments/aws-poc"
FOUNDATION_TERRAFORM_DIR="${REPO_ROOT}/infra/terraform/foundations/aws-eks"
FOUNDATION_STATE_PATH="${FOUNDATION_STATE_PATH:-${FOUNDATION_TERRAFORM_DIR}/terraform.tfstate}"
NAMESPACE="${NAMESPACE:-lakehouse}"
AWS_CLUSTER_NAME="${AWS_CLUSTER_NAME:-eks-openlakeforge-poc}"
KUBE_CONTEXT="${KUBE_CONTEXT:-}"
KUBECONFIG_PATH="${KUBECONFIG_PATH:-${REPO_ROOT}/.tmp/kubeconfigs/aws.yaml}"
DEPLOYMENT_SCOPE="${DEPLOYMENT_SCOPE:-aws}"
export HELM_CACHE_SCOPE="${HELM_CACHE_SCOPE:-${DEPLOYMENT_SCOPE}}"

# Account-mandated tags live in a .tfvars file rather than variable defaults.
TFVARS_FILE="${AWS_TFVARS_FILE:-${TERRAFORM_DIR}/sandbox.tfvars}"
TFVARS_ARGS=()
[[ -f "${TFVARS_FILE}" ]] && TFVARS_ARGS+=(-var-file="${TFVARS_FILE}")
PROJECT_CODE_IMAGE_PULL_POLICY="${PROJECT_CODE_IMAGE_PULL_POLICY:-Always}"
PROJECT_CODE_IMAGE_REVISION="${PROJECT_CODE_IMAGE_REVISION:-manual}"
SUPERSET_IMAGE_PULL_POLICY="${SUPERSET_IMAGE_PULL_POLICY:-Always}"
TRINO_CHART_REPOSITORY="${TRINO_CHART_REPOSITORY:-https://trinodb.github.io/charts}"
TRINO_CHART_VERSION="${TRINO_CHART_VERSION:-1.42.2}"
DAGSTER_CHART_REPOSITORY="${DAGSTER_CHART_REPOSITORY:-https://dagster-io.github.io/helm}"
DAGSTER_CHART_VERSION="${DAGSTER_CHART_VERSION:-1.13.6}"

RUN_RETRY_ATTEMPTS="${AWS_UP_RETRY_ATTEMPTS:-4}"
RUN_RETRY_DELAY_SECONDS="${AWS_UP_RETRY_DELAY_SECONDS:-20}"

# shellcheck source=scripts/lib/common.sh
source "${REPO_ROOT}/scripts/lib/common.sh"
# shellcheck source=scripts/lib/helm.sh
source "${REPO_ROOT}/scripts/lib/helm.sh"
# shellcheck source=scripts/lib/kube.sh
source "${REPO_ROOT}/scripts/lib/kube.sh"

TRINO_CHART_PACKAGE_PATH="${TRINO_CHART_PACKAGE_PATH:-${HELM_CHART_CACHE_DIR}/trino-${TRINO_CHART_VERSION}.tgz}"
DAGSTER_CHART_PACKAGE_PATH="${DAGSTER_CHART_PACKAGE_PATH:-${HELM_CHART_CACHE_DIR}/dagster-${DAGSTER_CHART_VERSION}-no-schema.tgz}"

prepare_eks_context() {
  if [[ ! -f "${FOUNDATION_STATE_PATH}" ]]; then
    echo "ERROR: AWS foundation Terraform state is missing: ${FOUNDATION_STATE_PATH}" >&2
    echo "Run 'make aws-foundation-up' before applying the AWS platform." >&2
    exit 1
  fi

  AWS_REGION="$(terraform -chdir="${FOUNDATION_TERRAFORM_DIR}" output -raw aws_region)"
  AWS_CLUSTER_NAME="$(terraform -chdir="${FOUNDATION_TERRAFORM_DIR}" output -raw cluster_name)"
  KUBE_CONTEXT="${KUBE_CONTEXT:-${AWS_CLUSTER_NAME}}"
  configure_deployment_scope

  aws eks update-kubeconfig \
    --region "${AWS_REGION}" \
    --name "${AWS_CLUSTER_NAME}" \
    --kubeconfig "${KUBECONFIG_PATH}" \
    --alias "${KUBE_CONTEXT}" >/dev/null

  if ! kubectl cluster-info --context "${KUBE_CONTEXT}" >/dev/null 2>&1; then
    echo "ERROR: Kubernetes context '${KUBE_CONTEXT}' is not reachable." >&2
    exit 1
  fi

  require_kube_context
}

prepare_image_variables() {
  AWS_IMAGE_TAG="${AWS_IMAGE_TAG:-aws-$(git_or_time_tag)}"
  PROJECT_CODE_IMAGE_REPOSITORY="${PROJECT_CODE_IMAGE_REPOSITORY:-$(terraform -chdir="${FOUNDATION_TERRAFORM_DIR}" output -raw project_code_ecr_repository_url)}"
  PROJECT_CODE_IMAGE_TAG="${PROJECT_CODE_IMAGE_TAG:-${AWS_IMAGE_TAG}}"
  SUPERSET_IMAGE_REPOSITORY="${SUPERSET_IMAGE_REPOSITORY:-$(terraform -chdir="${FOUNDATION_TERRAFORM_DIR}" output -raw superset_ecr_repository_url)}"
  SUPERSET_IMAGE_TAG="${SUPERSET_IMAGE_TAG:-${AWS_IMAGE_TAG}}"
}

prepare_superset_image() {
  echo "==> Building and pushing AWS Superset image before Helm install..."
  AWS_REGION="${AWS_REGION}" \
  AWS_IMAGE_TAG="${AWS_IMAGE_TAG}" \
  SUPERSET_IMAGE_REPOSITORY="${SUPERSET_IMAGE_REPOSITORY}" \
  SUPERSET_IMAGE_TAG="${SUPERSET_IMAGE_TAG}" \
    bash "${SCRIPT_DIR}/../images/build-push-superset.sh"
}

terraform_apply_once() {
  terraform -chdir="${TERRAFORM_DIR}" apply -auto-approve \
    ${TFVARS_ARGS[@]+"${TFVARS_ARGS[@]}"} \
    -var="namespace=${NAMESPACE}" \
    -var="aws_region=${AWS_REGION}" \
    -var="kube_context=${KUBE_CONTEXT}" \
    -var="kubeconfig_path=${KUBECONFIG_PATH}" \
    -var="foundation_state_path=${FOUNDATION_STATE_PATH}" \
    -var="project_code_image_repository=${PROJECT_CODE_IMAGE_REPOSITORY}" \
    -var="project_code_image_tag=${PROJECT_CODE_IMAGE_TAG}" \
    -var="project_code_image_pull_policy=${PROJECT_CODE_IMAGE_PULL_POLICY}" \
    -var="project_code_image_revision=${PROJECT_CODE_IMAGE_REVISION}" \
    -var="superset_image_repository=${SUPERSET_IMAGE_REPOSITORY}" \
    -var="superset_image_tag=${SUPERSET_IMAGE_TAG}" \
    -var="superset_image_pull_policy=${SUPERSET_IMAGE_PULL_POLICY}" \
    -var="trino_chart_package_path=${TRINO_CHART_PACKAGE_PATH}" \
    -var="dagster_chart_package_path=${DAGSTER_CHART_PACKAGE_PATH}"
}

echo "==> Checking AWS platform prerequisites..."
check_prereqs aws docker helm kubectl terraform
prepare_eks_context
prepare_image_variables
prepare_superset_image
prepare_helm_cache_dirs
prepare_cached_chart "Trino" trino "${TRINO_CHART_REPOSITORY}" trino/trino \
  "${TRINO_CHART_VERSION}" "${TRINO_CHART_PACKAGE_PATH}"
prepare_cached_dagster_chart_no_schema dagster "${DAGSTER_CHART_REPOSITORY}" \
  "${DAGSTER_CHART_VERSION}" "${DAGSTER_CHART_PACKAGE_PATH}"

echo "==> Initializing Terraform AWS POC platform..."
terraform -chdir="${TERRAFORM_DIR}" init
terraform_import_namespace_args=(
  ${TFVARS_ARGS[@]+"${TFVARS_ARGS[@]}"}
  -var="namespace=${NAMESPACE}"
  -var="aws_region=${AWS_REGION}"
  -var="kube_context=${KUBE_CONTEXT}"
  -var="kubeconfig_path=${KUBECONFIG_PATH}"
  -var="foundation_state_path=${FOUNDATION_STATE_PATH}"
  -var="project_code_image_repository=${PROJECT_CODE_IMAGE_REPOSITORY}"
  -var="project_code_image_tag=${PROJECT_CODE_IMAGE_TAG}"
  -var="project_code_image_pull_policy=${PROJECT_CODE_IMAGE_PULL_POLICY}"
  -var="project_code_image_revision=${PROJECT_CODE_IMAGE_REVISION}"
  -var="superset_image_repository=${SUPERSET_IMAGE_REPOSITORY}"
  -var="superset_image_tag=${SUPERSET_IMAGE_TAG}"
  -var="superset_image_pull_policy=${SUPERSET_IMAGE_PULL_POLICY}"
  -var="trino_chart_package_path=${TRINO_CHART_PACKAGE_PATH}"
  -var="dagster_chart_package_path=${DAGSTER_CHART_PACKAGE_PATH}"
)
import_namespace_if_missing_in_state \
  "${TERRAFORM_DIR}" \
  "kubernetes_namespace_v1.lakehouse" \
  "${NAMESPACE}" \
  "${terraform_import_namespace_args[@]}"

echo "==> Applying Terraform AWS POC platform..."
run_with_retry "Terraform apply" terraform_apply_once

echo "Static OpenLakeForge AWS POC platform is applied."
