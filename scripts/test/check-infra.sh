#!/usr/bin/env bash
set -euo pipefail

TERRAFORM_DIRS=(
  "infra/terraform/foundations/local-kind"
  "infra/terraform/foundations/azure-aks"
  "infra/terraform/foundations/aws-eks"
  "infra/terraform/environments/local"
  "infra/terraform/environments/azure-poc"
  "infra/terraform/environments/aws-poc"
)

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

for terraform_dir in "${TERRAFORM_DIRS[@]}"; do
  echo "==> Terraform init: ${terraform_dir}"
  terraform -chdir="${terraform_dir}" init -backend=false

  echo "==> Terraform validate: ${terraform_dir}"
  terraform -chdir="${terraform_dir}" validate
done

echo "==> Helm repo setup"
helm repo add seaweedfs https://seaweedfs.github.io/seaweedfs/helm --force-update >/dev/null
helm repo add polaris https://downloads.apache.org/polaris/helm-chart --force-update >/dev/null
helm repo add trino https://trinodb.github.io/charts --force-update >/dev/null
helm repo add dagster https://dagster-io.github.io/helm --force-update >/dev/null
helm repo add superset http://apache.github.io/superset/ --force-update >/dev/null
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

echo "==> Helm template: Superset"
superset_render="$(helm template superset superset/superset \
  --version 0.15.5 \
  --namespace lakehouse \
  --values infra/helm/values/local/superset.yaml \
  --set image.repository=ghcr.io/openlakeforge/superset \
  --set image.tag=local \
  --set image.pullPolicy=Never \
  --set extraSecretEnv.SUPERSET_SECRET_KEY=check \
  --set supersetNode.connections.db_host=postgresql \
  --set supersetNode.connections.db_port=5432 \
  --set supersetNode.connections.db_user=superset \
  --set supersetNode.connections.db_pass=check \
  --set supersetNode.connections.db_name=superset \
  --set-json 'extraVolumes=[{"name":"superset-reports","emptyDir":{"sizeLimit":"1Gi"}}]' \
  --set-json 'extraVolumeMounts=[{"name":"superset-reports","mountPath":"/app/openlakeforge/reports"}]')"

if ! grep -q 'emptyDir:' <<<"${superset_render}"; then
  echo "ERROR: rendered Superset chart is missing the ephemeral reports emptyDir." >&2
  exit 1
fi
if grep -q '^kind: PersistentVolumeClaim$' <<<"${superset_render}"; then
  echo "ERROR: rendered Superset chart still contains a reports PVC." >&2
  exit 1
fi

echo "Infrastructure checks passed."
