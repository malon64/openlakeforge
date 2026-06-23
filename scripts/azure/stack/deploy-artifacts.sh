#!/usr/bin/env bash
# Deploy dynamic Azure POC artifacts after the static infrastructure exists.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
FOUNDATION_TERRAFORM_DIR="${REPO_ROOT}/infra/terraform/foundations/azure-aks"
CONTRACT_TERRAFORM_DIR="${OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR:-${REPO_ROOT}/infra/terraform/environments/azure-poc}"
NAMESPACE="${NAMESPACE:-lakehouse}"
AZURE_CLUSTER_NAME="${AZURE_CLUSTER_NAME:-aks-openlakeforge-poc}"
KUBE_CONTEXT="${KUBE_CONTEXT:-}"

git_or_time_tag() {
  git -C "${REPO_ROOT}" rev-parse --short HEAD 2>/dev/null || date -u +%Y%m%d%H%M%S
}

require_cmd() {
  local cmd="$1"
  if ! command -v "${cmd}" &>/dev/null; then
    echo "ERROR: '${cmd}' not found on PATH" >&2
    exit 1
  fi
}

prepare_aks_context() {
  AZURE_RESOURCE_GROUP="$(terraform -chdir="${FOUNDATION_TERRAFORM_DIR}" output -raw resource_group_name)"
  AZURE_CLUSTER_NAME="$(terraform -chdir="${FOUNDATION_TERRAFORM_DIR}" output -raw cluster_name)"
  KUBE_CONTEXT="${KUBE_CONTEXT:-${AZURE_CLUSTER_NAME}}"

  az aks get-credentials \
    --resource-group "${AZURE_RESOURCE_GROUP}" \
    --name "${AZURE_CLUSTER_NAME}" \
    --overwrite-existing >/dev/null

  if ! kubectl cluster-info --context "${KUBE_CONTEXT}" >/dev/null 2>&1; then
    echo "ERROR: Kubernetes context '${KUBE_CONTEXT}' is not reachable." >&2
    echo "Run 'make azure-foundation-up' before deploying Azure artifacts." >&2
    exit 1
  fi

  kubectl config use-context "${KUBE_CONTEXT}" >/dev/null
}

prepare_image_variables() {
  ACR_LOGIN_SERVER="$(terraform -chdir="${FOUNDATION_TERRAFORM_DIR}" output -raw acr_login_server)"
  ACR_NAME="$(terraform -chdir="${FOUNDATION_TERRAFORM_DIR}" output -raw acr_name)"
  AZURE_IMAGE_TAG="${AZURE_IMAGE_TAG:-azure-$(git_or_time_tag)}"
  PROJECT_CODE_IMAGE_REPOSITORY="${PROJECT_CODE_IMAGE_REPOSITORY:-${ACR_LOGIN_SERVER}/openlakeforge/project-code}"
  PROJECT_CODE_IMAGE_TAG="${PROJECT_CODE_IMAGE_TAG:-${AZURE_IMAGE_TAG}}"
  PROJECT_CODE_IMAGE="${PROJECT_CODE_IMAGE_REPOSITORY}:${PROJECT_CODE_IMAGE_TAG}"
}

patch_dagster_instance_configmap() {
  local configmap="dagster-instance"

  if ! kubectl get configmap "${configmap}" -n "${NAMESPACE}" >/dev/null 2>&1; then
    return 0
  fi

  echo "==> Updating Dagster run launcher image to ${PROJECT_CODE_IMAGE}..."
  NAMESPACE="${NAMESPACE}" \
  CONFIGMAP_NAME="${configmap}" \
  PROJECT_CODE_IMAGE="${PROJECT_CODE_IMAGE}" \
    python3 - <<'PY'
import json
import os
import subprocess
import sys

namespace = os.environ["NAMESPACE"]
configmap = os.environ["CONFIGMAP_NAME"]
image = os.environ["PROJECT_CODE_IMAGE"]

raw = subprocess.check_output(
    ["kubectl", "get", "configmap", configmap, "-n", namespace, "-o", "json"],
    text=True,
)
payload = json.loads(raw)
data = payload.setdefault("data", {})
dagster_yaml = data.get("dagster.yaml")
if dagster_yaml is None:
    sys.exit(0)

lines = dagster_yaml.splitlines()
updated = False
for index, line in enumerate(lines):
    stripped = line.lstrip()
    if stripped.startswith("job_image:"):
        indent = line[: len(line) - len(stripped)]
        lines[index] = f'{indent}job_image: "{image}"'
        updated = True
        break

if not updated:
    sys.exit("ERROR: dagster-instance ConfigMap does not contain run launcher job_image.")

data["dagster.yaml"] = "\n".join(lines) + ("\n" if dagster_yaml.endswith("\n") else "")
patch = json.dumps({"data": {"dagster.yaml": data["dagster.yaml"]}})
subprocess.check_call(
    ["kubectl", "patch", "configmap", configmap, "-n", namespace, "--type", "merge", "-p", patch]
)
PY
}

patch_deployment_image_if_exists() {
  local deployment="$1"

  if ! kubectl get deployment "${deployment}" -n "${NAMESPACE}" >/dev/null 2>&1; then
    return 0
  fi

  echo "==> Updating ${deployment} image to ${PROJECT_CODE_IMAGE}..."
  kubectl set image "deployment/${deployment}" "*=${PROJECT_CODE_IMAGE}" -n "${NAMESPACE}"
}

discover_dagster_user_deployments() {
  kubectl get deployments -n "${NAMESPACE}" -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' \
    | grep -E 'dagster-user-deployments-.+-dagster$' || true
}

restart_if_exists() {
  local deployment="$1"

  if ! kubectl get deployment "${deployment}" -n "${NAMESPACE}" >/dev/null 2>&1; then
    return 0
  fi

  echo "==> Restarting ${deployment} after Azure artifact deployment..."
  kubectl rollout restart "deployment/${deployment}" -n "${NAMESPACE}"
  kubectl rollout status "deployment/${deployment}" -n "${NAMESPACE}" --timeout=600s
}

update_dagster_project_code_image() {
  patch_dagster_instance_configmap
  patch_deployment_image_if_exists "dagster-dagster-webserver"
  patch_deployment_image_if_exists "dagster-dagster-daemon"
  patch_deployment_image_if_exists "dagster-webserver"
  patch_deployment_image_if_exists "dagster-daemon"

  while IFS= read -r deployment; do
    patch_deployment_image_if_exists "${deployment}"
  done < <(discover_dagster_user_deployments)
}

restart_dagster_project_code_deployments() {
  restart_if_exists "dagster-dagster-webserver"
  restart_if_exists "dagster-dagster-daemon"
  restart_if_exists "dagster-webserver"
  restart_if_exists "dagster-daemon"

  while IFS= read -r deployment; do
    restart_if_exists "${deployment}"
  done < <(discover_dagster_user_deployments)
}

for cmd in az docker kubectl python3 terraform; do
  require_cmd "${cmd}"
done

prepare_aks_context
prepare_image_variables

echo "==> Generating product Floe manifests before baking the project-code image..."
NAMESPACE="${NAMESPACE}" \
OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR="${CONTRACT_TERRAFORM_DIR}" \
  bash "${REPO_ROOT}/scripts/local/artifacts/floe-manifest.sh"

echo "==> Building and pushing Azure project-code image..."
ACR_LOGIN_SERVER="${ACR_LOGIN_SERVER}" \
ACR_NAME="${ACR_NAME}" \
AZURE_IMAGE_TAG="${AZURE_IMAGE_TAG}" \
PROJECT_CODE_IMAGE_REPOSITORY="${PROJECT_CODE_IMAGE_REPOSITORY}" \
PROJECT_CODE_IMAGE_TAG="${PROJECT_CODE_IMAGE_TAG}" \
  bash "${SCRIPT_DIR}/../images/build-push-project-code.sh"

echo "==> Publishing product Floe manifests to the Azure POC SeaweedFS ops bucket..."
NAMESPACE="${NAMESPACE}" \
OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR="${CONTRACT_TERRAFORM_DIR}" \
  bash "${REPO_ROOT}/scripts/local/artifacts/upload-floe-manifest.sh"

echo "==> Deploying product Superset report assets..."
NAMESPACE="${NAMESPACE}" \
OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR="${CONTRACT_TERRAFORM_DIR}" \
  bash "${REPO_ROOT}/scripts/local/artifacts/superset-reports-deploy.sh"

echo "==> Deploying OpenMetadata governance metadata..."
NAMESPACE="${NAMESPACE}" \
OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR="${CONTRACT_TERRAFORM_DIR}" \
OPENMETADATA_ALLOW_MISSING_ASSETS="${OPENMETADATA_ALLOW_MISSING_ASSETS:-true}" \
  bash "${REPO_ROOT}/scripts/local/artifacts/openmetadata-metadata-deploy.sh"

update_dagster_project_code_image
restart_dagster_project_code_deployments

echo "Dynamic OpenLakeForge Azure POC artifacts are deployed."
