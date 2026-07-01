#!/usr/bin/env bash
# Build and push the OpenLakeForge project-code image to Amazon ECR.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
FOUNDATION_TERRAFORM_DIR="${REPO_ROOT}/infra/terraform/foundations/aws-eks"
source "${REPO_ROOT}/scripts/lib/docker.sh"

git_or_time_tag() {
  git -C "${REPO_ROOT}" rev-parse --short HEAD 2>/dev/null || date -u +%Y%m%d%H%M%S
}

for cmd in aws docker terraform; do
  if ! command -v "${cmd}" &>/dev/null; then
    echo "ERROR: '${cmd}' not found on PATH" >&2
    exit 1
  fi
done

AWS_REGION="${AWS_REGION:-$(terraform -chdir="${FOUNDATION_TERRAFORM_DIR}" output -raw aws_region 2>/dev/null || true)}"
PROJECT_CODE_IMAGE_REPOSITORY="${PROJECT_CODE_IMAGE_REPOSITORY:-$(terraform -chdir="${FOUNDATION_TERRAFORM_DIR}" output -raw project_code_ecr_repository_url 2>/dev/null || true)}"
if [[ -z "${AWS_REGION}" || -z "${PROJECT_CODE_IMAGE_REPOSITORY}" ]]; then
  echo "ERROR: AWS_REGION or PROJECT_CODE_IMAGE_REPOSITORY is unset and could not be read from the AWS foundation state." >&2
  exit 1
fi

AWS_IMAGE_TAG="${AWS_IMAGE_TAG:-aws-$(git_or_time_tag)}"
AWS_IMAGE_PLATFORM="${AWS_IMAGE_PLATFORM:-linux/amd64}"
PROJECT_CODE_IMAGE_TAG="${PROJECT_CODE_IMAGE_TAG:-${AWS_IMAGE_TAG}}"
PROJECT_CODE_PYTHON_BASE_IMAGE="${PROJECT_CODE_PYTHON_BASE_IMAGE:-public.ecr.aws/docker/library/python:3.12-slim}"
PROJECT_CODE_DBT_PROFILE_ENV="${PROJECT_CODE_DBT_PROFILE_ENV:-aws}"
IMAGE="${PROJECT_CODE_IMAGE_REPOSITORY}:${PROJECT_CODE_IMAGE_TAG}"
registry="${PROJECT_CODE_IMAGE_REPOSITORY%%/*}"

echo "==> Logging in to ECR '${registry}'..."
aws ecr get-login-password --region "${AWS_REGION}" \
  | docker login --username AWS --password-stdin "${registry}" >/dev/null

echo "==> Pulling project-code Python base image: ${PROJECT_CODE_PYTHON_BASE_IMAGE}"
docker_pull_with_retries --platform "${AWS_IMAGE_PLATFORM}" "${PROJECT_CODE_PYTHON_BASE_IMAGE}"

echo "==> Building project-code image ${IMAGE}..."
docker_build_with_retries \
  --platform "${AWS_IMAGE_PLATFORM}" \
  --build-arg "PYTHON_BASE_IMAGE=${PROJECT_CODE_PYTHON_BASE_IMAGE}" \
  --build-arg "DBT_PROFILE_ENV=${PROJECT_CODE_DBT_PROFILE_ENV}" \
  --file "${REPO_ROOT}/images/project-code/Dockerfile" \
  --tag "${IMAGE}" \
  "${REPO_ROOT}"

echo "==> Pushing project-code image ${IMAGE}..."
docker_push_with_retries "${IMAGE}"

echo "Pushed ${IMAGE}"
