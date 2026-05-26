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

  echo "==> Ensuring local project-code image is available to kind..."
  PROJECT_CODE_IMAGE_REPOSITORY="${PROJECT_CODE_IMAGE_REPOSITORY}" \
    PROJECT_CODE_IMAGE_TAG="${PROJECT_CODE_IMAGE_TAG}" \
    bash "${SCRIPT_DIR}/load-project-code-image.sh"
}

echo "==> Checking prerequisites..."
check_prereqs

prepare_local_project_code_image

echo "==> Applying Terraform local stack..."
terraform -chdir="${TERRAFORM_DIR}" init
terraform -chdir="${TERRAFORM_DIR}" apply \
  -auto-approve \
  -var="namespace=${NAMESPACE}" \
  -var="project_code_image_repository=${PROJECT_CODE_IMAGE_REPOSITORY}" \
  -var="project_code_image_tag=${PROJECT_CODE_IMAGE_TAG}" \
  -var="project_code_image_pull_policy=${PROJECT_CODE_IMAGE_PULL_POLICY}"

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
