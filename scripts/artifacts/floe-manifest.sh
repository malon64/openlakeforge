#!/usr/bin/env bash
# Generate product Floe Dagster manifests from the provider runtime profile.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
NAMESPACE="${NAMESPACE:-lakehouse}"
FLOE_PLATFORM="${FLOE_PLATFORM:-}"
USING_DOCKER="false"

# shellcheck source=scripts/lib/common.sh
source "${REPO_ROOT}/scripts/lib/common.sh"
# shellcheck source=scripts/lib/python.sh
source "${REPO_ROOT}/scripts/lib/python.sh"
# shellcheck source=/dev/null
source "${REPO_ROOT}/scripts/contracts/load-runtime-env.sh"

cd "${REPO_ROOT}"

default_floe_version="0.6.7"
FLOE_VERSION="${FLOE_VERSION:-${default_floe_version}}"
FLOE_IMAGE="${FLOE_IMAGE:-ghcr.io/malon64/floe:${FLOE_VERSION}}"
FLOE_RUNTIME="${FLOE_RUNTIME:-image}"

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

# Manifest generation is image-targeted by default because OpenLakeForge replays
# these manifests in separate Floe runner images. Set FLOE_RUNTIME=cli for
# same-host debugging where relative paths are preferable.
# Runner selection. Docker is the default so manifest generation does not require
# a host-installed Floe CLI; set FLOE_PREFER_CLI=true to use the native CLI.
FLOE_PREFER_CLI="${FLOE_PREFER_CLI:-false}"
floe_cli_version_matches() {
  command -v floe &>/dev/null || return 1
  [[ "$(floe --version 2>/dev/null || true)" == "floe ${FLOE_VERSION}"* ]]
}

if [[ "${FLOE_PREFER_CLI}" == "true" ]] && floe_cli_version_matches; then
  FLOE_CMD=(floe)
elif command -v docker &>/dev/null; then
  USING_DOCKER="true"
  FLOE_CMD=(docker run --rm)
  if [[ -n "${FLOE_PLATFORM}" ]]; then
    FLOE_CMD+=(--platform "${FLOE_PLATFORM}")
  fi
  for env_name in \
    AWS_ACCESS_KEY_ID \
    AWS_SECRET_ACCESS_KEY \
    AWS_SESSION_TOKEN \
    AWS_REGION \
    AWS_DEFAULT_REGION \
    AWS_ENDPOINT_URL \
    AWS_ENDPOINT_URL_S3 \
    AWS_S3_FORCE_PATH_STYLE \
    AWS_ALLOW_HTTP \
    AWS_EC2_METADATA_DISABLED
  do
    if [[ -n "${!env_name:-}" ]]; then
      FLOE_CMD+=(-e "${env_name}")
    fi
  done
  FLOE_CMD+=(-v "${REPO_ROOT}:/work" -w / "${FLOE_IMAGE}")
elif floe_cli_version_matches; then
  FLOE_CMD=(floe)
else
  echo "ERROR: floe ${FLOE_VERSION} CLI or Docker is required to generate manifests." >&2
  exit 1
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
    /*)
      printf '%s\n' "${path}"
      ;;
    *)
      printf '/work/%s\n' "${path}"
      ;;
  esac
}

PROFILE_PATH="${FLOE_PROFILE_PATH:-}"
GENERATED_PROFILE_PATH="${PROFILE_PATH}"
PROFILE_TMP_DIR=""
IS_AWS_FLOE_PROFILE="false"
FLOE_RUNTIME_ARTIFACT_DIR="${FLOE_RUNTIME_ARTIFACT_DIR:-.tmp/floe-runtime}"
PERSIST_RUNTIME_ARTIFACTS="${FLOE_PERSIST_RUNTIME_ARTIFACTS:-false}"
FLOE_REMOTE_RUNTIME_BASE_URI="${FLOE_REMOTE_RUNTIME_BASE_URI:-}"
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
    "${FLOE_RUNTIME_ARTIFACT_DIR}/configs" \
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
  olf_run floe render-profile > "${GENERATED_PROFILE_PATH}"
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
    if [[ "${PERSIST_RUNTIME_ARTIFACTS}" == "true" ]]; then
      profile_path="${FLOE_RUNTIME_ARTIFACT_DIR}/profiles/${domain}/${product}/$(basename "${profile_path}")"
      mkdir -p "$(dirname "${profile_path}")"
      cp "${GENERATED_PROFILE_PATH}" "${profile_path}"
    fi
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
    olf_run floe render-profile > "${profile_path}"

  printf '%s\n' "${profile_path}"
}

config_for_manifest() {
  local config_path="$1"
  local domain="$2"
  local product="$3"
  local runtime_config_path

  if [[ "${PERSIST_RUNTIME_ARTIFACTS}" != "true" ]]; then
    printf '%s\n' "${config_path}"
    return
  fi

  runtime_config_path="${FLOE_RUNTIME_ARTIFACT_DIR}/configs/${domain}/${product}/$(basename "${config_path}")"
  mkdir -p "$(dirname "${runtime_config_path}")"
  cp "${config_path}" "${runtime_config_path}"
  printf '%s\n' "${runtime_config_path}"
}

remote_runtime_uri() {
  local kind="$1"
  local domain="$2"
  local product="$3"
  local filename="$4"

  if [[ -z "${FLOE_REMOTE_RUNTIME_BASE_URI}" ]]; then
    return 1
  fi

  printf '%s/%s/%s/%s/%s\n' \
    "${FLOE_REMOTE_RUNTIME_BASE_URI%/}" \
    "${kind}" \
    "${domain}" \
    "${product}" \
    "${filename}"
}

generate_manifest() {
  local config_path="$1"
  local domain_dir="${config_path%/contracts/floe/*}"
  local product
  local domain
  local manifest_path
  local profile_path
  local generation_config_path
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
  generation_config_path="$(config_for_manifest "${config_path}" "${domain}" "${product}")"

  echo "==> Validating Floe config: ${config_path}"
  if floe_config_path="$(remote_runtime_uri "configs" "${domain}" "${product}" "$(basename "${generation_config_path}")")"; then
    floe_profile_path="$(remote_runtime_uri "profiles" "${domain}" "${product}" "$(basename "${profile_path}")")"
    floe_manifest_path="$(remote_runtime_uri "manifests" "${domain}" "${product}" "$(basename "${manifest_path}")")"
  else
    floe_config_path="$(floe_path "${generation_config_path}")"
    floe_profile_path="$(floe_path "${profile_path}")"
    floe_manifest_path="$(floe_path "${manifest_path}")"
  fi
  "${FLOE_CMD[@]}" validate -c "${floe_config_path}" -p "${floe_profile_path}"

  echo "==> Generating Floe manifest: ${manifest_path}"
  "${FLOE_CMD[@]}" manifest generate \
    -c "${floe_config_path}" \
    -p "${floe_profile_path}" \
    --deterministic \
    --manifest-name "${domain}.${product}.local" \
    --default-domain "${domain}_${product}" \
    --manifest-path-mode resolved-uri \
    --runtime "${FLOE_RUNTIME}" \
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
