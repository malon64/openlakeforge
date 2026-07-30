#!/usr/bin/env bash
# Deploy dynamic local domain artifacts after the static platform exists.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
NAMESPACE="${NAMESPACE:-lakehouse}"
CLUSTER_NAME="${CLUSTER_NAME:-openlakeforge-local}"
KUBE_CONTEXT="${KUBE_CONTEXT:-kind-${CLUSTER_NAME}}"
KUBECONFIG_PATH="${KUBECONFIG_PATH:-${REPO_ROOT}/.tmp/kubeconfigs/local.yaml}"
DEPLOYMENT_SCOPE="${DEPLOYMENT_SCOPE:-local}"
PROJECT_CODE_IMAGE_REPOSITORY="${PROJECT_CODE_IMAGE_REPOSITORY:-ghcr.io/openlakeforge/project-code}"
PROJECT_CODE_IMAGE_TAG="${PROJECT_CODE_IMAGE_TAG:-local}"
PROJECT_CODE_IMAGE="${PROJECT_CODE_IMAGE_REPOSITORY}:${PROJECT_CODE_IMAGE_TAG}"
FLOE_RUNTIME_ARTIFACT_DIR="${FLOE_RUNTIME_ARTIFACT_DIR:-${REPO_ROOT}/.tmp/floe-runtime/local}"
export OPENLAKEFORGE_REPO_ROOT="${REPO_ROOT}"

# shellcheck source=scripts/lib/common.sh
source "${REPO_ROOT}/scripts/lib/common.sh"
configure_deployment_scope
# shellcheck source=scripts/lib/kube.sh
source "${REPO_ROOT}/scripts/lib/kube.sh"
# shellcheck source=scripts/lib/python.sh
source "${REPO_ROOT}/scripts/lib/python.sh"

cd "${REPO_ROOT}"

prepare_local_project_code_image() {
  if [[ "${PROJECT_CODE_IMAGE_TAG}" != "local" ]]; then
    return 0
  fi

  require_cmd docker

  echo "==> Building local project-code image..."
  PROJECT_CODE_IMAGE_REPOSITORY="${PROJECT_CODE_IMAGE_REPOSITORY}" \
    PROJECT_CODE_IMAGE_TAG="${PROJECT_CODE_IMAGE_TAG}" \
    bash "${SCRIPT_DIR}/../images/build-project-code.sh"

  echo "==> Ensuring local project-code image is available to kind..."
  CLUSTER_NAME="${CLUSTER_NAME}" \
  PROJECT_CODE_IMAGE_REPOSITORY="${PROJECT_CODE_IMAGE_REPOSITORY}" \
    PROJECT_CODE_IMAGE_TAG="${PROJECT_CODE_IMAGE_TAG}" \
    bash "${SCRIPT_DIR}/../images/load-project-code.sh"
}

require_cmd kubectl
require_cmd uv

require_kube_context

# Load the provider contract environment for the olf artifact commands.
# shellcheck source=/dev/null
source "${REPO_ROOT}/scripts/contracts/load-runtime-env.sh"

echo "==> Generating local product Floe manifests for namespace '${NAMESPACE}'..."
export FLOE_RUNTIME_ARTIFACT_DIR
export FLOE_PERSIST_RUNTIME_ARTIFACTS="true"
NAMESPACE="${NAMESPACE}" bash "${REPO_ROOT}/scripts/artifacts/floe-manifest.sh"

# One artifact revision contract: the hash of the manifest set generated above
# is stamped into the image, onto the uploaded objects, and into the Dagster env,
# so a partial deploy cannot silently mix revisions. This is only sound when the
# image is actually (re)built from these manifests below (PROJECT_CODE_IMAGE_TAG=
# local, the default) -- a custom pre-built tag is not rebuilt by
# prepare_local_project_code_image, so nothing guarantees its baked
# OPENLAKEFORGE_FLOE_MANIFEST_REVISION_BUILT label matches manifests generated
# just now. Forcing that mismatch into Dagster's env would make a perfectly good
# pre-built image fail ArtifactRevisionError at startup. Respect an
# already-exported FLOE_MANIFEST_REVISION (the operator's own claim about what is
# baked into that image) instead; otherwise leave it unset ("manual"), which
# disables the runtime check rather than asserting something unverifiable.
if [[ "${PROJECT_CODE_IMAGE_TAG}" == "local" ]]; then
  FLOE_MANIFEST_REVISION="$(olf_run revision compute --runtime-root "${FLOE_RUNTIME_ARTIFACT_DIR}")"
  export FLOE_MANIFEST_REVISION
  echo "==> Floe artifact revision: ${FLOE_MANIFEST_REVISION}"
else
  FLOE_MANIFEST_REVISION="${FLOE_MANIFEST_REVISION:-manual}"
  export FLOE_MANIFEST_REVISION
  if [[ "${FLOE_MANIFEST_REVISION}" == "manual" ]]; then
    echo "==> PROJECT_CODE_IMAGE_TAG=${PROJECT_CODE_IMAGE_TAG} is not rebuilt here; artifact revision checking" \
      "stays disabled for it (export FLOE_MANIFEST_REVISION to enforce it against this image's baked value)."
  fi
fi

prepare_local_project_code_image

echo "==> Publishing product Floe manifests to local ops bucket..."
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

echo "==> Deploying product Superset report assets..."
olf_run superset deploy-reports

echo "==> Deploying OpenMetadata governance metadata..."
OPENMETADATA_ALLOW_MISSING_ASSETS="${OPENMETADATA_ALLOW_MISSING_ASSETS:-true}" \
  olf_run openmetadata deploy-metadata

echo "Dynamic OpenLakeForge local artifacts are deployed."
