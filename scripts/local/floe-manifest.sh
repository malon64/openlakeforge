#!/usr/bin/env bash
# Generate the Sales Floe Dagster manifest from the local Kubernetes profile.
set -euo pipefail

CONFIG_PATH="${FLOE_CONFIG_PATH:-domains/sales/contracts/floe/sales_poc.yml}"
PROFILE_PATH="${FLOE_PROFILE_PATH:-domains/sales/contracts/floe/profiles/local-k8s.yml}"
MANIFEST_PATH="${FLOE_MANIFEST_PATH:-domains/sales/contracts/floe/manifests/sales.manifest.json}"
NAMESPACE="${NAMESPACE:-lakehouse}"
FLOE_VERSION="${FLOE_VERSION:-0.4.5}"
FLOE_IMAGE="${FLOE_IMAGE:-ghcr.io/malon64/floe:${FLOE_VERSION}}"

if command -v docker &>/dev/null; then
  FLOE_CMD=(docker run --rm -v "${PWD}:/work" -w /work "${FLOE_IMAGE}")
else
  FLOE_CMD=(floe)
  if ! command -v floe &>/dev/null || [[ "$(floe --version 2>/dev/null || true)" != "floe ${FLOE_VERSION}" ]]; then
    echo "ERROR: Docker or Floe ${FLOE_VERSION} is required to generate the manifest." >&2
    exit 1
  fi
fi

mkdir -p "$(dirname "${MANIFEST_PATH}")"

GENERATED_PROFILE_PATH="${PROFILE_PATH}"
PROFILE_TMP_DIR=""
cleanup() {
  if [[ -n "${PROFILE_TMP_DIR}" ]]; then
    rm -rf "${PROFILE_TMP_DIR}"
  fi
}
trap cleanup EXIT

if [[ "${NAMESPACE}" != "lakehouse" ]]; then
  mkdir -p .tmp
  PROFILE_TMP_DIR="$(mktemp -d .tmp/floe-profile.XXXXXX)"
  GENERATED_PROFILE_PATH="${PROFILE_TMP_DIR}/local-k8s.yml"
  sed \
    -e "s|namespace: lakehouse|namespace: ${NAMESPACE}|g" \
    -e "s|http://lakehouse\\.svc\\.cluster\\.local:8333|http://${NAMESPACE}.svc.cluster.local:8333|g" \
    "${PROFILE_PATH}" > "${GENERATED_PROFILE_PATH}"
fi

echo "==> Validating Floe config: ${CONFIG_PATH}"
"${FLOE_CMD[@]}" validate -c "${CONFIG_PATH}" -p "${GENERATED_PROFILE_PATH}"

echo "==> Generating Floe manifest: ${MANIFEST_PATH}"
"${FLOE_CMD[@]}" manifest generate \
  -c "${CONFIG_PATH}" \
  -p "${GENERATED_PROFILE_PATH}" \
  --deterministic \
  --manifest-name sales.local \
  --default-domain sales \
  --manifest-path-mode resolved-uri \
  --output "${MANIFEST_PATH}"

echo "Generated ${MANIFEST_PATH}"
