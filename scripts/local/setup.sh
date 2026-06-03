#!/usr/bin/env bash
# Bring up the OpenLakeForge local stack on the current Kubernetes context.
#
# Terraform owns namespace creation, Helm releases, local credentials, bootstrap
# jobs, and service contracts. The kind cluster itself is still created by
# scripts/local/create-cluster.sh.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
TERRAFORM_DIR="${REPO_ROOT}/infra/terraform/environments/local"
NAMESPACE="${NAMESPACE:-lakehouse}"
PROJECT_CODE_IMAGE_REPOSITORY="${PROJECT_CODE_IMAGE_REPOSITORY:-ghcr.io/openlakeforge/project-code}"
PROJECT_CODE_IMAGE_TAG="${PROJECT_CODE_IMAGE_TAG:-local}"
PROJECT_CODE_IMAGE_PULL_POLICY="${PROJECT_CODE_IMAGE_PULL_POLICY:-IfNotPresent}"
PROJECT_CODE_IMAGE_REVISION="${PROJECT_CODE_IMAGE_REVISION:-manual}"

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

prepare_local_project_code_image() {
  if [[ "${PROJECT_CODE_IMAGE_TAG}" != "local" ]]; then
    return 0
  fi

  local image="${PROJECT_CODE_IMAGE_REPOSITORY}:${PROJECT_CODE_IMAGE_TAG}"

  if ! command -v docker &>/dev/null; then
    echo "ERROR: Docker is required when PROJECT_CODE_IMAGE_TAG=local." >&2
    echo "Run 'make project-code-image' and 'make project-code-load' from a shell with Docker access." >&2
    exit 1
  fi

  if ! docker image inspect "${image}" >/dev/null 2>&1; then
    echo "ERROR: local project-code image '${image}' does not exist." >&2
    echo "Run 'make project-code-image' before 'make local-up'." >&2
    exit 1
  fi

  PROJECT_CODE_IMAGE_REVISION="$(docker image inspect "${image}" --format '{{.Id}}')"

  echo "==> Ensuring local project-code image is available to kind..."
  PROJECT_CODE_IMAGE_REPOSITORY="${PROJECT_CODE_IMAGE_REPOSITORY}" \
    PROJECT_CODE_IMAGE_TAG="${PROJECT_CODE_IMAGE_TAG}" \
    bash "${SCRIPT_DIR}/load-project-code-image.sh"
}

refresh_ephemeral_polaris_bootstrap() {
  # Local Polaris uses the in-memory test metastore. If the Polaris pod restarts,
  # Kubernetes secrets can outlive the Polaris principals they refer to.
  echo "==> Refreshing Polaris local bootstrap principals..."
  kubectl delete job polaris-bootstrap-1 -n "${NAMESPACE}" --ignore-not-found=true
  terraform -chdir="${TERRAFORM_DIR}" apply \
    -auto-approve \
    -target=module.polaris.kubernetes_job_v1.bootstrap \
    -var="namespace=${NAMESPACE}" \
    -var="project_code_image_repository=${PROJECT_CODE_IMAGE_REPOSITORY}" \
    -var="project_code_image_tag=${PROJECT_CODE_IMAGE_TAG}" \
    -var="project_code_image_pull_policy=${PROJECT_CODE_IMAGE_PULL_POLICY}" \
    -var="project_code_image_revision=${PROJECT_CODE_IMAGE_REVISION}"

  echo "==> Restarting Trino so it reads refreshed Polaris credentials..."
  kubectl rollout restart deployment/trino-coordinator -n "${NAMESPACE}"
  kubectl rollout status deployment/trino-coordinator -n "${NAMESPACE}" --timeout=300s
}

restart_sales_code_server() {
  local deployment="dagster-user-deployments-sales-dagster"

  if ! kubectl get deployment "${deployment}" -n "${NAMESPACE}" >/dev/null 2>&1; then
    return 0
  fi

  echo "==> Restarting Sales Dagster code server after manifest upload..."
  kubectl rollout restart "deployment/${deployment}" -n "${NAMESPACE}"
  kubectl rollout status "deployment/${deployment}" -n "${NAMESPACE}" --timeout=300s
}

echo "==> Checking prerequisites..."
check_prereqs

echo "==> Generating local Floe manifest for namespace '${NAMESPACE}'..."
NAMESPACE="${NAMESPACE}" bash "${SCRIPT_DIR}/floe-manifest.sh"

prepare_local_project_code_image

echo "==> Applying Terraform local stack..."
terraform -chdir="${TERRAFORM_DIR}" init
terraform -chdir="${TERRAFORM_DIR}" apply \
  -auto-approve \
  -var="namespace=${NAMESPACE}" \
  -var="project_code_image_repository=${PROJECT_CODE_IMAGE_REPOSITORY}" \
  -var="project_code_image_tag=${PROJECT_CODE_IMAGE_TAG}" \
  -var="project_code_image_pull_policy=${PROJECT_CODE_IMAGE_PULL_POLICY}" \
  -var="project_code_image_revision=${PROJECT_CODE_IMAGE_REVISION}"

echo "==> Publishing Sales Floe manifest to local code bucket..."
NAMESPACE="${NAMESPACE}" bash "${SCRIPT_DIR}/upload-floe-manifest.sh"
restart_sales_code_server

refresh_ephemeral_polaris_bootstrap

echo ""
echo "OpenLakeForge local stack is up."
echo ""
echo "Port-forward commands:"
echo "  kubectl port-forward svc/seaweedfs-s3 9000:8333 -n ${NAMESPACE}"
echo "  kubectl port-forward svc/polaris 8181:8181 -n ${NAMESPACE}"
echo "  kubectl port-forward svc/trino 8080:8080 -n ${NAMESPACE}"
echo "  make local-forward"
echo ""
echo "Trino UI:     http://localhost:8080"
echo "Polaris API:  http://localhost:8181/api/catalog"
echo "SeaweedFS S3: http://localhost:9000"
echo "Dagster UI:   http://localhost:3000"
