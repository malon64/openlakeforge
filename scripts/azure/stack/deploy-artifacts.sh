#!/usr/bin/env bash
# Deploy dynamic Azure POC artifacts after the static platform exists.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
FOUNDATION_TERRAFORM_DIR="${REPO_ROOT}/infra/terraform/foundations/azure-aks"
CONTRACT_TERRAFORM_DIR="${OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR:-${REPO_ROOT}/infra/terraform/environments/azure-poc}"
NAMESPACE="${NAMESPACE:-lakehouse}"
AZURE_CLUSTER_NAME="${AZURE_CLUSTER_NAME:-aks-openlakeforge-poc}"
KUBE_CONTEXT="${KUBE_CONTEXT:-}"
KUBECONFIG_PATH="${KUBECONFIG_PATH:-${REPO_ROOT}/.tmp/kubeconfigs/azure.yaml}"
DEPLOYMENT_SCOPE="${DEPLOYMENT_SCOPE:-azure}"
FLOE_RUNTIME_ARTIFACT_DIR="${FLOE_RUNTIME_ARTIFACT_DIR:-${REPO_ROOT}/.tmp/floe-runtime/azure}"
export OPENLAKEFORGE_REPO_ROOT="${REPO_ROOT}"
export OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR="${CONTRACT_TERRAFORM_DIR}"

# shellcheck source=scripts/lib/common.sh
source "${REPO_ROOT}/scripts/lib/common.sh"
# shellcheck source=scripts/lib/kube.sh
source "${REPO_ROOT}/scripts/lib/kube.sh"
# shellcheck source=scripts/lib/python.sh
source "${REPO_ROOT}/scripts/lib/python.sh"

cd "${REPO_ROOT}"

prepare_aks_context() {
  AZURE_RESOURCE_GROUP="$(terraform -chdir="${FOUNDATION_TERRAFORM_DIR}" output -raw resource_group_name)"
  AZURE_CLUSTER_NAME="$(terraform -chdir="${FOUNDATION_TERRAFORM_DIR}" output -raw cluster_name)"
  KUBE_CONTEXT="${KUBE_CONTEXT:-${AZURE_CLUSTER_NAME}}"
  configure_deployment_scope

  az aks get-credentials \
    --resource-group "${AZURE_RESOURCE_GROUP}" \
    --name "${AZURE_CLUSTER_NAME}" \
    --file "${KUBECONFIG_PATH}" \
    --overwrite-existing >/dev/null

  if ! kubectl cluster-info --context "${KUBE_CONTEXT}" >/dev/null 2>&1; then
    echo "ERROR: Kubernetes context '${KUBE_CONTEXT}' is not reachable." >&2
    echo "Run 'make azure-foundation-up' before deploying Azure artifacts." >&2
    exit 1
  fi

  require_kube_context
}

prepare_image_variables() {
  ACR_LOGIN_SERVER="$(terraform -chdir="${FOUNDATION_TERRAFORM_DIR}" output -raw acr_login_server)"
  ACR_NAME="$(terraform -chdir="${FOUNDATION_TERRAFORM_DIR}" output -raw acr_name)"
  AZURE_IMAGE_TAG="${AZURE_IMAGE_TAG:-azure-$(git_or_time_tag)}"
  PROJECT_CODE_IMAGE_REPOSITORY="${PROJECT_CODE_IMAGE_REPOSITORY:-${ACR_LOGIN_SERVER}/openlakeforge/project-code}"
  PROJECT_CODE_IMAGE_TAG="${PROJECT_CODE_IMAGE_TAG:-${AZURE_IMAGE_TAG}}"
  PROJECT_CODE_IMAGE="${PROJECT_CODE_IMAGE_REPOSITORY}:${PROJECT_CODE_IMAGE_TAG}"
}

for cmd in az docker kubectl uv terraform; do
  require_cmd "${cmd}"
done

prepare_aks_context
prepare_image_variables

# Load the provider contract environment for the olf artifact commands.
# shellcheck source=/dev/null
source "${REPO_ROOT}/scripts/contracts/load-runtime-env.sh"

echo "==> Generating product Floe manifests before baking the project-code image..."
export FLOE_RUNTIME_ARTIFACT_DIR
export FLOE_PERSIST_RUNTIME_ARTIFACTS="true"
NAMESPACE="${NAMESPACE}" bash "${REPO_ROOT}/scripts/artifacts/floe-manifest.sh"

# One artifact revision contract: the hash of the manifest set generated above
# is stamped into the image, onto the uploaded objects, and into the Dagster env,
# so a partial deploy cannot silently mix revisions.
FLOE_MANIFEST_REVISION="$(olf_run revision compute --runtime-root "${FLOE_RUNTIME_ARTIFACT_DIR}")"
export FLOE_MANIFEST_REVISION
echo "==> Floe artifact revision: ${FLOE_MANIFEST_REVISION}"

echo "==> Building and pushing Azure project-code image..."
ACR_LOGIN_SERVER="${ACR_LOGIN_SERVER}" \
ACR_NAME="${ACR_NAME}" \
AZURE_IMAGE_TAG="${AZURE_IMAGE_TAG}" \
PROJECT_CODE_IMAGE_REPOSITORY="${PROJECT_CODE_IMAGE_REPOSITORY}" \
PROJECT_CODE_IMAGE_TAG="${PROJECT_CODE_IMAGE_TAG}" \
FLOE_MANIFEST_REVISION="${FLOE_MANIFEST_REVISION}" \
  bash "${SCRIPT_DIR}/../images/build-push-project-code.sh"

echo "==> Publishing product Floe manifests to the Azure POC SeaweedFS ops bucket..."
olf_run artifacts upload-manifests --via port-forward --runtime-root "${FLOE_RUNTIME_ARTIFACT_DIR}"
olf_run revision publish --runtime-root "${FLOE_RUNTIME_ARTIFACT_DIR}" --via port-forward

# Roll Dagster onto the new image/revision immediately after the bucket holds it,
# before any other deploy step. The manifest URIs are mutable, so once the bucket
# is updated any Floe runner pod dereferencing them gets the new content
# regardless of whether Dagster's code server has restarted -- a Superset or
# OpenMetadata failure between publish and rollout would otherwise leave the
# code server serving stale definitions while runners already see the new
# manifests, recreating the exact skew this contract exists to prevent.
echo "==> Pointing Dagster at project-code image ${PROJECT_CODE_IMAGE}..."
olf_run k8s set-project-code-image \
  --image "${PROJECT_CODE_IMAGE}" \
  --floe-manifest-revision "${FLOE_MANIFEST_REVISION}"

# Persist the just-deployed revision as an auto-loaded tfvars input. Terraform
# only knows floe_manifest_revision through -var on this apply; the patch above
# is applied directly via kubectl, so any *later*, unrelated `terraform apply`
# against this root (a routine Helm chart bump, for example) would otherwise
# reset the variable to its "manual" default and silently disable
# ArtifactRevisionError checking until the next artifact deploy re-patches it.
# Terraform auto-loads any *.auto.tfvars file in its working directory, so this
# makes the deployed revision part of the root's desired state without a
# manual -var-file flag.
echo "floe_manifest_revision = \"${FLOE_MANIFEST_REVISION}\"" \
  > "${CONTRACT_TERRAFORM_DIR}/olf-artifact-revision.auto.tfvars"

echo "==> Deploying product Superset report assets..."
olf_run superset deploy-reports

echo "==> Deploying OpenMetadata governance metadata..."
OPENMETADATA_ALLOW_MISSING_ASSETS="${OPENMETADATA_ALLOW_MISSING_ASSETS:-true}" \
  olf_run openmetadata deploy-metadata

echo "Dynamic OpenLakeForge Azure POC artifacts are deployed."
