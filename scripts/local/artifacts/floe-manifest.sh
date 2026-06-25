#!/usr/bin/env bash
# Generate product Floe Dagster manifests from the provider runtime profile.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
NAMESPACE="${NAMESPACE:-lakehouse}"
FLOE_VERSION="${FLOE_VERSION:-0.5.4}"
FLOE_IMAGE="${FLOE_IMAGE:-ghcr.io/malon64/floe:${FLOE_VERSION}}"

# shellcheck source=/dev/null
source "${REPO_ROOT}/scripts/local/contracts/load-runtime-env.sh"

if [[ -z "${FLOE_RUNTIME_PROFILE_URI:-}" ]]; then
  if [[ "${OPENLAKEFORGE_STORAGE_IMPLEMENTATION}" == "storage.aws_s3" &&
    "${OPENLAKEFORGE_CATALOG_TYPE}" == "glue" &&
    "${OPENLAKEFORGE_CATALOG_PROVIDER}" == "aws-glue" ]]; then
    FLOE_RUNTIME_PROFILE_URI="local:///work/libs/floe/profiles/aws-eks.yml"
  else
    FLOE_RUNTIME_PROFILE_URI="local:///work/libs/floe/profiles/local-k8s.yml"
  fi
fi
export FLOE_RUNTIME_PROFILE_URI

if command -v docker &>/dev/null; then
  FLOE_CMD=(docker run --rm -v "${PWD}:/work" -w /work "${FLOE_IMAGE}")
else
  FLOE_CMD=(floe)
  if ! command -v floe &>/dev/null || [[ "$(floe --version 2>/dev/null || true)" != "floe ${FLOE_VERSION}" ]]; then
    echo "ERROR: Docker or Floe ${FLOE_VERSION} is required to generate manifests." >&2
    exit 1
  fi
fi

PROFILE_PATH="${FLOE_PROFILE_PATH:-}"
GENERATED_PROFILE_PATH="${PROFILE_PATH}"
PROFILE_TMP_DIR=""
CONFIG_TMP_DIR=""
GENERATED_CONFIG_PATH=""
IS_AWS_FLOE_PROFILE="false"
cleanup() {
  if [[ -n "${PROFILE_TMP_DIR}" ]]; then
    rm -rf "${PROFILE_TMP_DIR}"
  fi
  if [[ -n "${CONFIG_TMP_DIR}" ]]; then
    rm -rf "${CONFIG_TMP_DIR}"
  fi
}
trap cleanup EXIT

if [[ "${OPENLAKEFORGE_STORAGE_IMPLEMENTATION}" == "storage.aws_s3" &&
  "${OPENLAKEFORGE_CATALOG_TYPE}" == "glue" &&
  "${OPENLAKEFORGE_CATALOG_PROVIDER}" == "aws-glue" ]]; then
  IS_AWS_FLOE_PROFILE="true"
fi

if [[ -z "${PROFILE_PATH}" && "${IS_AWS_FLOE_PROFILE}" == "false" && "${NAMESPACE}" == "lakehouse" ]]; then
  GENERATED_PROFILE_PATH="libs/floe/profiles/local-k8s.yml"
elif [[ -z "${PROFILE_PATH}" ]]; then
  mkdir -p .tmp
  PROFILE_TMP_DIR="$(mktemp -d .tmp/floe-profile.XXXXXX)"
  GENERATED_PROFILE_PATH="${PROFILE_TMP_DIR}/$(basename "${FLOE_RUNTIME_PROFILE_URI}")"
  python3 "${REPO_ROOT}/scripts/local/contracts/render-floe-profile.py" > "${GENERATED_PROFILE_PATH}"
elif [[ "${NAMESPACE}" != "lakehouse" ]]; then
  mkdir -p .tmp
  PROFILE_TMP_DIR="$(mktemp -d .tmp/floe-profile.XXXXXX)"
  GENERATED_PROFILE_PATH="${PROFILE_TMP_DIR}/$(basename "${FLOE_RUNTIME_PROFILE_URI}")"
  sed \
    -e "s|namespace: lakehouse|namespace: ${NAMESPACE}|g" \
    -e "s|http://lakehouse\\.svc\\.cluster\\.local:8333|http://${NAMESPACE}.svc.cluster.local:8333|g" \
    "${PROFILE_PATH}" > "${GENERATED_PROFILE_PATH}"
fi

discover_configs() {
  if [[ -n "${FLOE_CONFIG_PATH:-}" ]]; then
    printf '%s\n' "${FLOE_CONFIG_PATH}"
    return
  fi

  find domains -path "*/contracts/floe/*.yml" -type f | sort
}

render_config() {
  local config_path="$1"

  if [[ "${OPENLAKEFORGE_STORAGE_BRONZE_BUCKET}" == "lakehouse-bronze" &&
    "${OPENLAKEFORGE_STORAGE_SILVER_BUCKET}" == "lakehouse-silver" &&
    "${OPENLAKEFORGE_OPS_BUCKET_NAME}" == "openlakeforge-ops" &&
    "${OPENLAKEFORGE_STORAGE_REGION}" == "us-east-1" ]]; then
    GENERATED_CONFIG_PATH="${config_path}"
    return
  fi

  if [[ -z "${CONFIG_TMP_DIR}" ]]; then
    mkdir -p .tmp
    CONFIG_TMP_DIR="$(mktemp -d .tmp/floe-config.XXXXXX)"
  fi

  GENERATED_CONFIG_PATH="${CONFIG_TMP_DIR}/${config_path}"
  mkdir -p "$(dirname "${GENERATED_CONFIG_PATH}")"

  python3 - "${config_path}" "${GENERATED_CONFIG_PATH}" <<'PY'
import os
import sys
from pathlib import Path

source = Path(sys.argv[1])
target = Path(sys.argv[2])
text = source.read_text(encoding="utf-8")
# Temporary compatibility renderer for provider-specific storage values.
# Upstream issue: https://github.com/malon64/floe/issues/424
replacements = {
    'bucket: "lakehouse-bronze"': f'bucket: "{os.environ["OPENLAKEFORGE_STORAGE_BRONZE_BUCKET"]}"',
    'bucket: "lakehouse-silver"': f'bucket: "{os.environ["OPENLAKEFORGE_STORAGE_SILVER_BUCKET"]}"',
    'bucket: "openlakeforge-ops"': f'bucket: "{os.environ["OPENLAKEFORGE_OPS_BUCKET_NAME"]}"',
    'region: "us-east-1"': f'region: "{os.environ["OPENLAKEFORGE_STORAGE_REGION"]}"',
}
for placeholder, value in replacements.items():
    text = text.replace(placeholder, value)
target.write_text(text, encoding="utf-8")
PY
}

generate_manifest() {
  local config_path="$1"
  local domain_dir="${config_path%/contracts/floe/*}"
  local product
  local domain
  local manifest_path
  local runtime_config_path
  product="$(basename "${config_path}" .yml)"
  domain="$(basename "${domain_dir}")"
  manifest_path="${FLOE_MANIFEST_PATH:-${domain_dir}/contracts/floe/manifests/${product}.manifest.json}"
  render_config "${config_path}"
  runtime_config_path="${GENERATED_CONFIG_PATH}"

  mkdir -p "$(dirname "${manifest_path}")"

  echo "==> Validating Floe config: ${config_path}"
  "${FLOE_CMD[@]}" validate -c "${runtime_config_path}" -p "${GENERATED_PROFILE_PATH}"

  echo "==> Generating Floe manifest: ${manifest_path}"
  "${FLOE_CMD[@]}" manifest generate \
    -c "${runtime_config_path}" \
    -p "${GENERATED_PROFILE_PATH}" \
    --deterministic \
    --manifest-name "${domain}.${product}.local" \
    --default-domain "${domain}_${product}" \
    --manifest-path-mode resolved-uri \
    --output "${manifest_path}"

  echo "Generated ${manifest_path}"
}

configs=()
while IFS= read -r config_path; do
  configs+=("${config_path}")
done < <(discover_configs)
if [[ "${#configs[@]}" -eq 0 ]]; then
  echo "ERROR: no product Floe configs found." >&2
  exit 1
fi

for config_path in "${configs[@]}"; do
  generate_manifest "${config_path}"
done
