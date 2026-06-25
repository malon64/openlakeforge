#!/usr/bin/env bash
# Build and push the OpenLakeForge Superset image to Amazon ECR.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
FOUNDATION_TERRAFORM_DIR="${REPO_ROOT}/infra/terraform/foundations/aws-eks"

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
SUPERSET_IMAGE_REPOSITORY="${SUPERSET_IMAGE_REPOSITORY:-$(terraform -chdir="${FOUNDATION_TERRAFORM_DIR}" output -raw superset_ecr_repository_url 2>/dev/null || true)}"
if [[ -z "${AWS_REGION}" || -z "${SUPERSET_IMAGE_REPOSITORY}" ]]; then
  echo "ERROR: AWS_REGION or SUPERSET_IMAGE_REPOSITORY is unset and could not be read from the AWS foundation state." >&2
  exit 1
fi

AWS_IMAGE_TAG="${AWS_IMAGE_TAG:-aws-$(git_or_time_tag)}"
AWS_IMAGE_PLATFORM="${AWS_IMAGE_PLATFORM:-linux/amd64}"
SUPERSET_IMAGE_TAG="${SUPERSET_IMAGE_TAG:-${AWS_IMAGE_TAG}}"
IMAGE="${SUPERSET_IMAGE_REPOSITORY}:${SUPERSET_IMAGE_TAG}"
registry="${SUPERSET_IMAGE_REPOSITORY%%/*}"

echo "==> Logging in to ECR '${registry}'..."
aws ecr get-login-password --region "${AWS_REGION}" \
  | docker login --username AWS --password-stdin "${registry}" >/dev/null

echo "==> Building Superset image ${IMAGE}..."
docker build \
  --platform "${AWS_IMAGE_PLATFORM}" \
  --file "${REPO_ROOT}/images/superset/Dockerfile" \
  --tag "${IMAGE}" \
  "${REPO_ROOT}/images/superset"

echo "==> Pushing Superset image ${IMAGE}..."
docker push "${IMAGE}"

echo "Pushed ${IMAGE}"
