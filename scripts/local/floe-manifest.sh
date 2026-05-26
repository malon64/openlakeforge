#!/usr/bin/env bash
# Generate the Sales Floe Dagster manifest from the local Kubernetes profile.
set -euo pipefail

CONFIG_PATH="${FLOE_CONFIG_PATH:-domains/sales/contracts/floe/sales_poc.yml}"
PROFILE_PATH="${FLOE_PROFILE_PATH:-domains/sales/contracts/floe/profiles/local-k8s.yml}"
MANIFEST_PATH="${FLOE_MANIFEST_PATH:-domains/sales/contracts/floe/manifests/sales.manifest.json}"

if ! command -v floe &>/dev/null; then
  echo "ERROR: 'floe' not found on PATH. Install it locally, for example with Homebrew, before building project-code." >&2
  exit 1
fi

mkdir -p "$(dirname "${MANIFEST_PATH}")"

echo "==> Validating Floe config: ${CONFIG_PATH}"
floe validate -c "${CONFIG_PATH}" -p "${PROFILE_PATH}"

echo "==> Generating Floe manifest: ${MANIFEST_PATH}"
floe manifest generate \
  -c "${CONFIG_PATH}" \
  -p "${PROFILE_PATH}" \
  --output "${MANIFEST_PATH}"

echo "Generated ${MANIFEST_PATH}"
