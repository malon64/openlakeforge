#!/usr/bin/env bash
# Generate product Floe Dagster manifests from the provider runtime profile.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
NAMESPACE="${NAMESPACE:-lakehouse}"
FLOE_VERSION="${FLOE_VERSION:-0.6.3}"
FLOE_IMAGE="${FLOE_IMAGE:-ghcr.io/malon64/floe:${FLOE_VERSION}}"
USING_DOCKER="false"

# shellcheck source=/dev/null
source "${REPO_ROOT}/scripts/local/contracts/load-runtime-env.sh"

cd "${REPO_ROOT}"

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
  USING_DOCKER="true"
  FLOE_CMD=(docker run --rm -v "${REPO_ROOT}:/work" -w /work "${FLOE_IMAGE}")
else
  FLOE_CMD=(floe)
  if ! command -v floe &>/dev/null || [[ "$(floe --version 2>/dev/null || true)" != "floe ${FLOE_VERSION}" ]]; then
    echo "ERROR: Docker or Floe ${FLOE_VERSION} is required to generate manifests." >&2
    exit 1
  fi
fi

floe_path() {
  local path="$1"

  if [[ "${USING_DOCKER}" != "true" ]]; then
    printf '%s\n' "${path}"
    return
  fi

  case "${path}" in
    "${REPO_ROOT}")
      printf '/work\n'
      ;;
    "${REPO_ROOT}/"*)
      printf '/work/%s\n' "${path#"${REPO_ROOT}/"}"
      ;;
    *)
      printf '%s\n' "${path}"
      ;;
  esac
}

PROFILE_PATH="${FLOE_PROFILE_PATH:-}"
GENERATED_PROFILE_PATH="${PROFILE_PATH}"
PROFILE_TMP_DIR=""
IS_AWS_FLOE_PROFILE="false"
FLOE_RUNTIME_ARTIFACT_DIR="${FLOE_RUNTIME_ARTIFACT_DIR:-.tmp/floe-runtime}"
PERSIST_RUNTIME_ARTIFACTS="${FLOE_PERSIST_RUNTIME_ARTIFACTS:-false}"
cleanup() {
  if [[ -n "${PROFILE_TMP_DIR}" ]]; then
    rm -rf "${PROFILE_TMP_DIR}"
  fi
}
trap cleanup EXIT

if [[ "${OPENLAKEFORGE_STORAGE_IMPLEMENTATION}" == "storage.aws_s3" &&
  "${OPENLAKEFORGE_CATALOG_TYPE}" == "glue" &&
  "${OPENLAKEFORGE_CATALOG_PROVIDER}" == "aws-glue" ]]; then
  IS_AWS_FLOE_PROFILE="true"
fi
if [[ "${PERSIST_RUNTIME_ARTIFACTS}" == "true" ]]; then
  rm -rf "${FLOE_RUNTIME_ARTIFACT_DIR}"
  mkdir -p \
    "${FLOE_RUNTIME_ARTIFACT_DIR}/manifests" \
    "${FLOE_RUNTIME_ARTIFACT_DIR}/profiles"
fi

if [[ -z "${PROFILE_PATH}" && "${IS_AWS_FLOE_PROFILE}" == "false" && "${NAMESPACE}" == "lakehouse" ]]; then
  GENERATED_PROFILE_PATH="libs/floe/profiles/local-k8s.yml"
elif [[ -z "${PROFILE_PATH}" && "${IS_AWS_FLOE_PROFILE}" == "true" ]]; then
  if [[ "${PERSIST_RUNTIME_ARTIFACTS}" != "true" ]]; then
    mkdir -p .tmp
    PROFILE_TMP_DIR="$(mktemp -d .tmp/floe-profile.XXXXXX)"
  fi
elif [[ -z "${PROFILE_PATH}" ]]; then
  if [[ "${PERSIST_RUNTIME_ARTIFACTS}" == "true" ]]; then
    GENERATED_PROFILE_PATH="${FLOE_RUNTIME_ARTIFACT_DIR}/profiles/$(basename "${FLOE_RUNTIME_PROFILE_URI}")"
  else
    mkdir -p .tmp
    PROFILE_TMP_DIR="$(mktemp -d .tmp/floe-profile.XXXXXX)"
    GENERATED_PROFILE_PATH="${PROFILE_TMP_DIR}/$(basename "${FLOE_RUNTIME_PROFILE_URI}")"
  fi
  python3 "${REPO_ROOT}/scripts/local/contracts/render-floe-profile.py" > "${GENERATED_PROFILE_PATH}"
elif [[ "${NAMESPACE}" != "lakehouse" ]]; then
  if [[ "${PERSIST_RUNTIME_ARTIFACTS}" == "true" ]]; then
    GENERATED_PROFILE_PATH="${FLOE_RUNTIME_ARTIFACT_DIR}/profiles/$(basename "${FLOE_RUNTIME_PROFILE_URI}")"
  else
    mkdir -p .tmp
    PROFILE_TMP_DIR="$(mktemp -d .tmp/floe-profile.XXXXXX)"
    GENERATED_PROFILE_PATH="${PROFILE_TMP_DIR}/$(basename "${FLOE_RUNTIME_PROFILE_URI}")"
  fi
  sed \
    -e "s|namespace: lakehouse|namespace: ${NAMESPACE}|g" \
    -e "s|http://lakehouse\\.svc\\.cluster\\.local:8333|http://${NAMESPACE}.svc.cluster.local:8333|g" \
    "${PROFILE_PATH}" > "${GENERATED_PROFILE_PATH}"
elif [[ "${PERSIST_RUNTIME_ARTIFACTS}" == "true" ]]; then
  GENERATED_PROFILE_PATH="${FLOE_RUNTIME_ARTIFACT_DIR}/profiles/$(basename "${PROFILE_PATH}")"
  cp "${PROFILE_PATH}" "${GENERATED_PROFILE_PATH}"
fi

discover_configs() {
  if [[ -n "${FLOE_CONFIG_PATH:-}" ]]; then
    printf '%s\n' "${FLOE_CONFIG_PATH}"
    return
  fi

  find domains -path "*/contracts/floe/*.yml" -type f | sort
}

silver_namespace_for_product() {
  local product_key="$1"

  OPENLAKEFORGE_PRODUCT_KEY="${product_key}" python3 - <<'PY'
import json
import os
import sys

product_key = os.environ["OPENLAKEFORGE_PRODUCT_KEY"]
try:
    namespaces = json.loads(os.environ.get("OPENLAKEFORGE_CATALOG_SILVER_NAMESPACES_JSON", "{}"))
except json.JSONDecodeError as exc:
    raise SystemExit(f"ERROR: invalid OPENLAKEFORGE_CATALOG_SILVER_NAMESPACES_JSON: {exc}") from exc

namespace = namespaces.get(product_key)
if not namespace:
    raise SystemExit(f"ERROR: missing Silver catalog namespace for product {product_key}")
sys.stdout.write(namespace)
PY
}

profile_for_config() {
  local domain="$1"
  local product="$2"
  local product_key="${domain}_${product}"
  local profile_path="${GENERATED_PROFILE_PATH}"
  local silver_namespace

  if [[ -n "${PROFILE_PATH}" || "${IS_AWS_FLOE_PROFILE}" != "true" ]]; then
    printf '%s\n' "${profile_path}"
    return
  fi

  silver_namespace="$(silver_namespace_for_product "${product_key}")"
  if [[ "${PERSIST_RUNTIME_ARTIFACTS}" == "true" ]]; then
    profile_path="${FLOE_RUNTIME_ARTIFACT_DIR}/profiles/${domain}/${product}/$(basename "${FLOE_RUNTIME_PROFILE_URI}")"
  else
    profile_path="${PROFILE_TMP_DIR}/${domain}/${product}/$(basename "${FLOE_RUNTIME_PROFILE_URI}")"
  fi

  mkdir -p "$(dirname "${profile_path}")"
  echo "==> Rendering AWS Floe profile for ${product_key} with Glue database ${silver_namespace}" >&2
  OPENLAKEFORGE_CATALOG_GLUE_DATABASE="${silver_namespace}" \
    python3 "${REPO_ROOT}/scripts/local/contracts/render-floe-profile.py" > "${profile_path}"

  printf '%s\n' "${profile_path}"
}

generate_manifest() {
  local config_path="$1"
  local domain_dir="${config_path%/contracts/floe/*}"
  local product
  local domain
  local manifest_path
  local profile_path
  local floe_config_path
  local floe_profile_path
  local floe_manifest_path
  product="$(basename "${config_path}" .yml)"
  domain="$(basename "${domain_dir}")"
  manifest_path="${FLOE_MANIFEST_PATH:-${domain_dir}/contracts/floe/manifests/${product}.manifest.json}"

  if [[ -z "${FLOE_MANIFEST_PATH:-}" && "${PERSIST_RUNTIME_ARTIFACTS}" == "true" ]]; then
    manifest_path="${FLOE_RUNTIME_ARTIFACT_DIR}/manifests/${domain}/${product}/${product}.manifest.json"
  fi

  mkdir -p "$(dirname "${manifest_path}")"
  profile_path="$(profile_for_config "${domain}" "${product}")"

  echo "==> Validating Floe config: ${config_path}"
  floe_config_path="$(floe_path "${config_path}")"
  floe_profile_path="$(floe_path "${profile_path}")"
  floe_manifest_path="$(floe_path "${manifest_path}")"
  "${FLOE_CMD[@]}" validate -c "${floe_config_path}" -p "${floe_profile_path}"

  echo "==> Generating Floe manifest: ${manifest_path}"
  "${FLOE_CMD[@]}" manifest generate \
    -c "${floe_config_path}" \
    -p "${floe_profile_path}" \
    --deterministic \
    --manifest-name "${domain}.${product}.local" \
    --default-domain "${domain}_${product}" \
    --manifest-path-mode resolved-uri \
    --output "${floe_manifest_path}"

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
