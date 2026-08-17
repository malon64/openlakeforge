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
# Set by `olf install run` to the cosign-verified digest; empty for
# make local-up (dev tags aren't signed releases, so there is nothing to
# pin against). When present, it -- not the mutable version tag -- is what
# actually gets deployed: a tag can be moved after publication without
# moving the digest, so deploying by tag alone would not actually run the
# bytes cosign verified.
PROJECT_CODE_IMAGE_DIGEST="${PROJECT_CODE_IMAGE_DIGEST:-}"
if [[ -n "${PROJECT_CODE_IMAGE_DIGEST}" ]]; then
  PROJECT_CODE_IMAGE="${PROJECT_CODE_IMAGE_REPOSITORY}@${PROJECT_CODE_IMAGE_DIGEST}"
else
  PROJECT_CODE_IMAGE="${PROJECT_CODE_IMAGE_REPOSITORY}:${PROJECT_CODE_IMAGE_TAG}"
fi
SUPERSET_IMAGE_REPOSITORY="${SUPERSET_IMAGE_REPOSITORY:-ghcr.io/openlakeforge/superset}"
SUPERSET_IMAGE_DIGEST="${SUPERSET_IMAGE_DIGEST:-}"
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

# Namespaces must exist before Floe writes the first Iceberg table into them.
echo "==> Reconciling Polaris namespaces from the domain descriptors..."
olf_run catalog sync-namespaces

echo "==> Generating local product Floe manifests for namespace '${NAMESPACE}'..."
export FLOE_RUNTIME_ARTIFACT_DIR
export FLOE_PERSIST_RUNTIME_ARTIFACTS="true"
NAMESPACE="${NAMESPACE}" bash "${REPO_ROOT}/scripts/artifacts/floe-manifest.sh"

echo "==> Publishing and verifying immutable Floe runtime-artifact revision..."
FLOE_MANIFEST_REVISION="$(olf_run revision activate --via port-forward --runtime-root "${FLOE_RUNTIME_ARTIFACT_DIR}")"
export FLOE_MANIFEST_REVISION

prepare_local_project_code_image

if [[ "${PROJECT_CODE_IMAGE_TAG}" != "local" ]]; then
  echo "==> Publishing legacy Floe manifests for the supplied project-code image..."
  olf_run artifacts upload-manifests --via port-forward --runtime-root "${FLOE_RUNTIME_ARTIFACT_DIR}"
fi

echo "==> Pointing Dagster at project-code image ${PROJECT_CODE_IMAGE}..."
olf_run k8s set-project-code-image --image "${PROJECT_CODE_IMAGE}"

# The Superset Helm chart has no digest-pinning support at all (unlike
# Dagster's chart), so this is the only way to run it pinned by digest
# rather than by the mutable tag Terraform/Helm deployed it with. Gated on
# the digest being set (only true for `olf install run`) so make local-up,
# which never has a release digest to pin to, is unaffected.
if [[ -n "${SUPERSET_IMAGE_DIGEST}" ]]; then
  superset_image="${SUPERSET_IMAGE_REPOSITORY}@${SUPERSET_IMAGE_DIGEST}"
  echo "==> Pointing Superset at verified digest ${superset_image}..."
  olf_run k8s set-superset-image --image "${superset_image}"
fi

OPENMETADATA_ALLOW_MISSING_ASSETS="${OPENMETADATA_ALLOW_MISSING_ASSETS:-true}" \
  olf_run artifacts deploy-optional-layers

echo "Dynamic OpenLakeForge local artifacts are deployed."
