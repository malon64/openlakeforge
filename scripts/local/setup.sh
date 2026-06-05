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
PROJECT_CODE_IMAGE_PULL_POLICY="${PROJECT_CODE_IMAGE_PULL_POLICY:-Never}"
PROJECT_CODE_IMAGE_REVISION="${PROJECT_CODE_IMAGE_REVISION:-manual}"
SUPERSET_IMAGE_REPOSITORY="${SUPERSET_IMAGE_REPOSITORY:-ghcr.io/openlakeforge/superset}"
SUPERSET_IMAGE_TAG="${SUPERSET_IMAGE_TAG:-local}"
SUPERSET_IMAGE_PULL_POLICY="${SUPERSET_IMAGE_PULL_POLICY:-Never}"

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

  echo "==> Building local project-code image..."
  PROJECT_CODE_IMAGE_REPOSITORY="${PROJECT_CODE_IMAGE_REPOSITORY}" \
    PROJECT_CODE_IMAGE_TAG="${PROJECT_CODE_IMAGE_TAG}" \
    bash "${SCRIPT_DIR}/build-project-code-image.sh"

  PROJECT_CODE_IMAGE_REVISION="$(docker image inspect "${image}" --format '{{.Id}}')"

  echo "==> Ensuring local project-code image is available to kind..."
  PROJECT_CODE_IMAGE_REPOSITORY="${PROJECT_CODE_IMAGE_REPOSITORY}" \
    PROJECT_CODE_IMAGE_TAG="${PROJECT_CODE_IMAGE_TAG}" \
    bash "${SCRIPT_DIR}/load-project-code-image.sh"
}

prepare_local_superset_image() {
  if [[ "${SUPERSET_IMAGE_TAG}" != "local" ]]; then
    return 0
  fi

  local image="${SUPERSET_IMAGE_REPOSITORY}:${SUPERSET_IMAGE_TAG}"

  if ! command -v docker &>/dev/null; then
    echo "ERROR: Docker is required when SUPERSET_IMAGE_TAG=local." >&2
    echo "Run 'make superset-image' and 'make superset-load' from a shell with Docker access." >&2
    exit 1
  fi

  echo "==> Building local Superset image..."
  SUPERSET_IMAGE_REPOSITORY="${SUPERSET_IMAGE_REPOSITORY}" \
    SUPERSET_IMAGE_TAG="${SUPERSET_IMAGE_TAG}" \
    bash "${SCRIPT_DIR}/build-superset-image.sh"

  echo "==> Ensuring local Superset image is available to kind..."
  SUPERSET_IMAGE_REPOSITORY="${SUPERSET_IMAGE_REPOSITORY}" \
    SUPERSET_IMAGE_TAG="${SUPERSET_IMAGE_TAG}" \
    bash "${SCRIPT_DIR}/load-superset-image.sh"
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
    -var="project_code_image_revision=${PROJECT_CODE_IMAGE_REVISION}" \
    -var="superset_image_repository=${SUPERSET_IMAGE_REPOSITORY}" \
    -var="superset_image_tag=${SUPERSET_IMAGE_TAG}" \
    -var="superset_image_pull_policy=${SUPERSET_IMAGE_PULL_POLICY}"

  echo "==> Restarting Trino so it reads refreshed Polaris credentials..."
  kubectl rollout restart deployment/trino-coordinator -n "${NAMESPACE}"
  kubectl rollout status deployment/trino-coordinator -n "${NAMESPACE}" --timeout=300s
}

restart_sales_code_server() {
  local deployment="dagster-dagster-user-deployments-sales-dagster"

  if ! kubectl get deployment "${deployment}" -n "${NAMESPACE}" >/dev/null 2>&1; then
    deployment="dagster-user-deployments-sales-dagster"

    if ! kubectl get deployment "${deployment}" -n "${NAMESPACE}" >/dev/null 2>&1; then
      return 0
    fi
  fi

  echo "==> Restarting Sales Dagster code server after manifest upload and Polaris refresh..."
  kubectl rollout restart "deployment/${deployment}" -n "${NAMESPACE}"
  kubectl rollout status "deployment/${deployment}" -n "${NAMESPACE}" --timeout=300s
}

apply_foundation_stack() {
  echo "==> Applying Terraform foundation services..."
  terraform -chdir="${TERRAFORM_DIR}" apply \
    -auto-approve \
    -target=module.seaweedfs \
    -target=module.postgresql \
    -target=module.polaris \
    -target=module.trino \
    -var="namespace=${NAMESPACE}" \
    -var="project_code_image_repository=${PROJECT_CODE_IMAGE_REPOSITORY}" \
    -var="project_code_image_tag=${PROJECT_CODE_IMAGE_TAG}" \
    -var="project_code_image_pull_policy=${PROJECT_CODE_IMAGE_PULL_POLICY}" \
    -var="project_code_image_revision=${PROJECT_CODE_IMAGE_REVISION}" \
    -var="superset_image_repository=${SUPERSET_IMAGE_REPOSITORY}" \
    -var="superset_image_tag=${SUPERSET_IMAGE_TAG}" \
    -var="superset_image_pull_policy=${SUPERSET_IMAGE_PULL_POLICY}"
}

refresh_openmetadata_bootstrap() {
  echo "==> Refreshing OpenMetadata local bootstrap..."
  kubectl delete job -n "${NAMESPACE}" \
    -l app.kubernetes.io/name=openmetadata,openlakeforge.io/component=governance \
    --ignore-not-found=true
  terraform -chdir="${TERRAFORM_DIR}" apply \
    -auto-approve \
    -target=module.openmetadata.kubernetes_job_v1.bootstrap \
    -var="namespace=${NAMESPACE}" \
    -var="project_code_image_repository=${PROJECT_CODE_IMAGE_REPOSITORY}" \
    -var="project_code_image_tag=${PROJECT_CODE_IMAGE_TAG}" \
    -var="project_code_image_pull_policy=${PROJECT_CODE_IMAGE_PULL_POLICY}" \
    -var="project_code_image_revision=${PROJECT_CODE_IMAGE_REVISION}" \
    -var="superset_image_repository=${SUPERSET_IMAGE_REPOSITORY}" \
    -var="superset_image_tag=${SUPERSET_IMAGE_TAG}" \
    -var="superset_image_pull_policy=${SUPERSET_IMAGE_PULL_POLICY}"

  if kubectl get deployment openmetadata-openlineage -n "${NAMESPACE}" >/dev/null 2>&1; then
    echo "==> Restarting OpenLineage proxy so it reads refreshed OpenMetadata bot credentials..."
    kubectl rollout restart deployment/openmetadata-openlineage -n "${NAMESPACE}"
    kubectl rollout status deployment/openmetadata-openlineage -n "${NAMESPACE}" --timeout=300s
  fi
}

echo "==> Checking prerequisites..."
check_prereqs

echo "==> Generating local Floe manifest for namespace '${NAMESPACE}'..."
NAMESPACE="${NAMESPACE}" bash "${SCRIPT_DIR}/floe-manifest.sh"

prepare_local_project_code_image
prepare_local_superset_image

echo "==> Applying Terraform local stack..."
terraform -chdir="${TERRAFORM_DIR}" init
apply_foundation_stack

echo "==> Publishing Sales Floe manifest to local code bucket..."
NAMESPACE="${NAMESPACE}" bash "${SCRIPT_DIR}/upload-floe-manifest.sh"

refresh_ephemeral_polaris_bootstrap
refresh_openmetadata_bootstrap

terraform -chdir="${TERRAFORM_DIR}" apply \
  -auto-approve \
  -var="namespace=${NAMESPACE}" \
  -var="project_code_image_repository=${PROJECT_CODE_IMAGE_REPOSITORY}" \
  -var="project_code_image_tag=${PROJECT_CODE_IMAGE_TAG}" \
  -var="project_code_image_pull_policy=${PROJECT_CODE_IMAGE_PULL_POLICY}" \
  -var="project_code_image_revision=${PROJECT_CODE_IMAGE_REVISION}" \
  -var="superset_image_repository=${SUPERSET_IMAGE_REPOSITORY}" \
  -var="superset_image_tag=${SUPERSET_IMAGE_TAG}" \
  -var="superset_image_pull_policy=${SUPERSET_IMAGE_PULL_POLICY}"

echo "==> Deploying Sales Superset report assets..."
NAMESPACE="${NAMESPACE}" bash "${SCRIPT_DIR}/superset-reports-deploy.sh"

restart_sales_code_server

echo ""
echo "OpenLakeForge local stack is up."
echo ""
echo "Run 'make local-forward' to port-forward all services, then open:"
echo ""
echo "  Dagster UI:       http://localhost:3000"
echo "  Superset UI:      http://localhost:8088  (admin / admin)"
echo "  OpenMetadata UI:  http://localhost:8585  (admin@open-metadata.org / admin)"
echo "  Trino UI:         http://localhost:8080"
echo "  Polaris API:      http://localhost:8181/api/catalog"
echo "  SeaweedFS S3:     http://localhost:9000"
