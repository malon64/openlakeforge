#!/usr/bin/env bash
# Deploy dynamic AWS POC artifacts after the static infrastructure exists.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
FOUNDATION_TERRAFORM_DIR="${REPO_ROOT}/infra/terraform/foundations/aws-eks"
CONTRACT_TERRAFORM_DIR="${OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR:-${REPO_ROOT}/infra/terraform/environments/aws-poc}"
NAMESPACE="${NAMESPACE:-lakehouse}"
AWS_CLUSTER_NAME="${AWS_CLUSTER_NAME:-eks-openlakeforge-poc}"
KUBE_CONTEXT="${KUBE_CONTEXT:-}"
export OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR="${CONTRACT_TERRAFORM_DIR}"

cd "${REPO_ROOT}"

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

prepare_eks_context() {
  AWS_REGION="$(terraform -chdir="${FOUNDATION_TERRAFORM_DIR}" output -raw aws_region)"
  AWS_CLUSTER_NAME="$(terraform -chdir="${FOUNDATION_TERRAFORM_DIR}" output -raw cluster_name)"
  KUBE_CONTEXT="${KUBE_CONTEXT:-${AWS_CLUSTER_NAME}}"

  aws eks update-kubeconfig \
    --region "${AWS_REGION}" \
    --name "${AWS_CLUSTER_NAME}" \
    --alias "${KUBE_CONTEXT}" >/dev/null

  if ! kubectl cluster-info --context "${KUBE_CONTEXT}" >/dev/null 2>&1; then
    echo "ERROR: Kubernetes context '${KUBE_CONTEXT}' is not reachable." >&2
    exit 1
  fi

  kubectl config use-context "${KUBE_CONTEXT}" >/dev/null
}

prepare_image_variables() {
  AWS_IMAGE_TAG="${AWS_IMAGE_TAG:-aws-$(git_or_time_tag)}"
  PROJECT_CODE_IMAGE_REPOSITORY="${PROJECT_CODE_IMAGE_REPOSITORY:-$(terraform -chdir="${FOUNDATION_TERRAFORM_DIR}" output -raw project_code_ecr_repository_url)}"
  PROJECT_CODE_IMAGE_TAG="${PROJECT_CODE_IMAGE_TAG:-${AWS_IMAGE_TAG}}"
  PROJECT_CODE_IMAGE="${PROJECT_CODE_IMAGE_REPOSITORY}:${PROJECT_CODE_IMAGE_TAG}"
}

patch_dagster_instance_configmap() {
  local configmap="dagster-instance"
  if ! kubectl get configmap "${configmap}" -n "${NAMESPACE}" >/dev/null 2>&1; then
    return 0
  fi

  echo "==> Updating Dagster run launcher image to ${PROJECT_CODE_IMAGE}..."
  NAMESPACE="${NAMESPACE}" CONFIGMAP_NAME="${configmap}" PROJECT_CODE_IMAGE="${PROJECT_CODE_IMAGE}" python3 - <<'PY'
import json
import os
import subprocess
import sys

namespace = os.environ["NAMESPACE"]
configmap = os.environ["CONFIGMAP_NAME"]
image = os.environ["PROJECT_CODE_IMAGE"]
raw = subprocess.check_output(["kubectl", "get", "configmap", configmap, "-n", namespace, "-o", "json"], text=True)
payload = json.loads(raw)
dagster_yaml = payload.setdefault("data", {}).get("dagster.yaml")
if dagster_yaml is None:
    sys.exit(0)
lines = dagster_yaml.splitlines()
for index, line in enumerate(lines):
    stripped = line.lstrip()
    if stripped.startswith("job_image:"):
        indent = line[: len(line) - len(stripped)]
        lines[index] = f'{indent}job_image: "{image}"'
        break
else:
    sys.exit("ERROR: dagster-instance ConfigMap does not contain run launcher job_image.")
patch = json.dumps({"data": {"dagster.yaml": "\n".join(lines) + ("\n" if dagster_yaml.endswith("\n") else "")}})
subprocess.check_call(["kubectl", "patch", "configmap", configmap, "-n", namespace, "--type", "merge", "-p", patch])
PY
}

patch_deployment_image_if_exists() {
  local deployment="$1"
  if kubectl get deployment "${deployment}" -n "${NAMESPACE}" >/dev/null 2>&1; then
    echo "==> Updating ${deployment} image to ${PROJECT_CODE_IMAGE}..."
    NAMESPACE="${NAMESPACE}" DEPLOYMENT_NAME="${deployment}" PROJECT_CODE_IMAGE="${PROJECT_CODE_IMAGE}" python3 - <<'PY'
import json
import os
import subprocess

namespace = os.environ["NAMESPACE"]
deployment = os.environ["DEPLOYMENT_NAME"]
image = os.environ["PROJECT_CODE_IMAGE"]
raw = subprocess.check_output(["kubectl", "get", "deployment", deployment, "-n", namespace, "-o", "json"], text=True)
payload = json.loads(raw)
containers = payload["spec"]["template"]["spec"].get("containers", [])
if not containers:
    raise SystemExit(f"ERROR: deployment/{deployment} has no regular containers to patch.")
patch = {
    "spec": {
        "template": {
            "spec": {
                "containers": [
                    {"name": container["name"], "image": image}
                    for container in containers
                ]
            }
        }
    }
}
subprocess.check_call([
    "kubectl",
    "patch",
    "deployment",
    deployment,
    "-n",
    namespace,
    "--type",
    "strategic",
    "-p",
    json.dumps(patch),
])
PY
  fi
}

patch_cronjob_image_if_exists() {
  local cronjob="$1"
  if ! kubectl get cronjob "${cronjob}" -n "${NAMESPACE}" >/dev/null 2>&1; then
    return 0
  fi

  echo "==> Updating ${cronjob} image to ${PROJECT_CODE_IMAGE}..."
  NAMESPACE="${NAMESPACE}" CRONJOB_NAME="${cronjob}" PROJECT_CODE_IMAGE="${PROJECT_CODE_IMAGE}" python3 - <<'PY'
import json
import os
import subprocess

namespace = os.environ["NAMESPACE"]
cronjob = os.environ["CRONJOB_NAME"]
image = os.environ["PROJECT_CODE_IMAGE"]
raw = subprocess.check_output(["kubectl", "get", "cronjob", cronjob, "-n", namespace, "-o", "json"], text=True)
payload = json.loads(raw)
containers = payload["spec"]["jobTemplate"]["spec"]["template"]["spec"].get("containers", [])
if not containers:
    raise SystemExit(f"ERROR: cronjob/{cronjob} has no regular containers to patch.")
patch = {
    "spec": {
        "jobTemplate": {
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {"name": container["name"], "image": image}
                            for container in containers
                        ]
                    }
                }
            }
        }
    }
}
subprocess.check_call([
    "kubectl",
    "patch",
    "cronjob",
    cronjob,
    "-n",
    namespace,
    "--type",
    "strategic",
    "-p",
    json.dumps(patch),
])
PY
}

discover_dagster_user_deployments() {
  kubectl get deployments -n "${NAMESPACE}" -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' \
    | grep -E 'dagster-user-deployments-.+-dagster$' || true
}

restart_if_exists() {
  local deployment="$1"
  if kubectl get deployment "${deployment}" -n "${NAMESPACE}" >/dev/null 2>&1; then
    echo "==> Restarting ${deployment} after AWS artifact deployment..."
    kubectl rollout restart "deployment/${deployment}" -n "${NAMESPACE}"
    kubectl rollout status "deployment/${deployment}" -n "${NAMESPACE}" --timeout=600s
  fi
}

update_dagster_project_code_image() {
  patch_dagster_instance_configmap
  patch_deployment_image_if_exists "dagster-dagster-webserver"
  patch_deployment_image_if_exists "dagster-dagster-daemon"
  patch_deployment_image_if_exists "dagster-webserver"
  patch_deployment_image_if_exists "dagster-daemon"
  patch_cronjob_image_if_exists "openlakeforge-k8s-log-archive"

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

upload_floe_manifests_to_s3() {
  # shellcheck source=/dev/null
  source "${REPO_ROOT}/scripts/local/contracts/load-runtime-env.sh"

  local bucket="${CODE_BUCKET_NAME:-${OPENLAKEFORGE_ARTIFACT_BUCKET_NAME}}"
  local root="${FLOE_RUNTIME_ARTIFACT_DIR:-${REPO_ROOT}/.tmp/floe-runtime/aws}"
  local manifest_root="${root}/manifests"
  local manifests=()
  if [[ ! -d "${manifest_root}" ]]; then
    echo "ERROR: no rendered AWS Floe manifests found under ${manifest_root}. Run manifest generation first." >&2
    exit 1
  fi

  while IFS= read -r manifest_path; do
    manifests+=("${manifest_path}")
  done < <(find "${manifest_root}" -name '*.manifest.json' -type f | sort)

  if [[ "${#manifests[@]}" -eq 0 ]]; then
    echo "ERROR: no rendered AWS Floe manifests found under ${manifest_root}. Run manifest generation first." >&2
    exit 1
  fi

  for manifest_path in "${manifests[@]}"; do
    local relative_path product domain key
    relative_path="${manifest_path#${manifest_root}/}"
    domain="${relative_path%%/*}"
    product="$(basename "${manifest_path}" .manifest.json)"
    key="floe/manifests/${domain}/${product}/${product}.manifest.json"
    aws s3api put-object \
      --bucket "${bucket}" \
      --key "${key}" \
      --body "${manifest_path}" \
      --content-type application/json >/dev/null
    echo "Published ${manifest_path} to s3://${bucket}/${key}"
  done
}

for cmd in aws docker kubectl python3 terraform; do
  require_cmd "${cmd}"
done

prepare_eks_context
prepare_image_variables

# shellcheck source=/dev/null
source "${REPO_ROOT}/scripts/local/contracts/load-runtime-env.sh"
FLOE_RUNTIME_ARTIFACT_DIR="${FLOE_RUNTIME_ARTIFACT_DIR:-.tmp/floe-runtime/aws}"
FLOE_PERSIST_RUNTIME_ARTIFACTS="true"
export FLOE_RUNTIME_ARTIFACT_DIR FLOE_PERSIST_RUNTIME_ARTIFACTS

echo "==> Generating product Floe manifests before baking the project-code image..."
NAMESPACE="${NAMESPACE}" \
  bash "${REPO_ROOT}/scripts/local/artifacts/floe-manifest.sh"

echo "==> Building and pushing AWS project-code image..."
AWS_REGION="${AWS_REGION}" \
AWS_IMAGE_TAG="${AWS_IMAGE_TAG}" \
PROJECT_CODE_IMAGE_REPOSITORY="${PROJECT_CODE_IMAGE_REPOSITORY}" \
PROJECT_CODE_IMAGE_TAG="${PROJECT_CODE_IMAGE_TAG}" \
  bash "${SCRIPT_DIR}/../images/build-push-project-code.sh"

echo "==> Publishing product Floe manifests to the AWS S3 ops bucket..."
upload_floe_manifests_to_s3

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

echo "Dynamic OpenLakeForge AWS POC artifacts are deployed."
