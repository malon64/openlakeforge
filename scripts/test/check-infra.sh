#!/usr/bin/env bash
set -euo pipefail

TERRAFORM_DIR="infra/terraform/environments/local"

require_cmd() {
  local cmd="$1"
  if ! command -v "${cmd}" &>/dev/null; then
    printf "ERROR: '%s' not found on PATH\n" "${cmd}" >&2
    exit 1
  fi
}

require_cmd terraform
require_cmd helm

echo "==> Terraform fmt"
terraform fmt -check -recursive infra/terraform

echo "==> Terraform init"
terraform -chdir="${TERRAFORM_DIR}" init -backend=false

echo "==> Terraform validate"
terraform -chdir="${TERRAFORM_DIR}" validate

echo "==> Helm repo setup"
helm repo add seaweedfs https://seaweedfs.github.io/seaweedfs/helm --force-update >/dev/null
helm repo add polaris https://downloads.apache.org/polaris/helm-chart --force-update >/dev/null
helm repo add trino https://trinodb.github.io/charts --force-update >/dev/null
helm repo add dagster https://dagster-io.github.io/helm --force-update >/dev/null
helm repo update >/dev/null

echo "==> Helm template: SeaweedFS"
helm template seaweedfs seaweedfs/seaweedfs \
  --version 4.23.0 \
  --namespace lakehouse \
  --values infra/helm/values/local/seaweedfs.yaml >/dev/null

echo "==> Helm template: Polaris"
helm template polaris polaris/polaris \
  --version 1.4.1 \
  --namespace lakehouse \
  --values infra/helm/values/local/polaris.yaml >/dev/null

echo "==> Helm template: Trino"
helm template trino trino/trino \
  --version 1.42.2 \
  --namespace lakehouse \
  --values infra/helm/values/local/trino.yaml >/dev/null

echo "==> Helm template: Dagster"
helm template dagster dagster/dagster \
  --version 1.13.6 \
  --namespace lakehouse \
  --values infra/helm/values/local/dagster.yaml >/dev/null

echo "Infrastructure checks passed."
