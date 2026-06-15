#!/usr/bin/env bash
# Generate product Floe Dagster manifests from the shared local Kubernetes profile.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
NAMESPACE="${NAMESPACE:-lakehouse}"
FLOE_VERSION="${FLOE_VERSION:-0.5.4}"
FLOE_IMAGE="${FLOE_IMAGE:-ghcr.io/malon64/floe:${FLOE_VERSION}}"

# shellcheck source=/dev/null
source "${REPO_ROOT}/scripts/local/contracts/load-runtime-env.sh"

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
cleanup() {
  if [[ -n "${PROFILE_TMP_DIR}" ]]; then
    rm -rf "${PROFILE_TMP_DIR}"
  fi
}
trap cleanup EXIT

if [[ -z "${PROFILE_PATH}" ]]; then
  mkdir -p .tmp
  PROFILE_TMP_DIR="$(mktemp -d .tmp/floe-profile.XXXXXX)"
  GENERATED_PROFILE_PATH="${PROFILE_TMP_DIR}/local-k8s.yml"
  python3 "${REPO_ROOT}/scripts/local/contracts/render-floe-profile.py" > "${GENERATED_PROFILE_PATH}"
elif [[ "${NAMESPACE}" != "lakehouse" ]]; then
  mkdir -p .tmp
  PROFILE_TMP_DIR="$(mktemp -d .tmp/floe-profile.XXXXXX)"
  GENERATED_PROFILE_PATH="${PROFILE_TMP_DIR}/local-k8s.yml"
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

generate_manifest() {
  local config_path="$1"
  local domain_dir="${config_path%/contracts/floe/*}"
  local product
  local domain
  local manifest_path
  product="$(basename "${config_path}" .yml)"
  domain="$(basename "${domain_dir}")"
  manifest_path="${FLOE_MANIFEST_PATH:-${domain_dir}/contracts/floe/manifests/${product}.manifest.json}"

  mkdir -p "$(dirname "${manifest_path}")"

  echo "==> Validating Floe config: ${config_path}"
  "${FLOE_CMD[@]}" validate -c "${config_path}" -p "${GENERATED_PROFILE_PATH}"

  echo "==> Generating Floe manifest: ${manifest_path}"
  "${FLOE_CMD[@]}" manifest generate \
    -c "${config_path}" \
    -p "${GENERATED_PROFILE_PATH}" \
    --deterministic \
    --manifest-name "${domain}.${product}.local" \
    --default-domain "${domain}_${product}" \
    --manifest-path-mode resolved-uri \
    --output "${manifest_path}"

  if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 is required to patch the generated Floe manifest." >&2
    exit 1
  fi

  python3 - "${manifest_path}" <<'PY'
import json
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
payload = json.loads(manifest_path.read_text(encoding="utf-8"))
base_args = payload["execution"]["base_args"]

if "--run-id" not in base_args:
    base_args.extend(["--run-id", "{run_id}"])

manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY

  echo "Generated ${manifest_path}"
}

mapfile -t configs < <(discover_configs)
if [[ "${#configs[@]}" -eq 0 ]]; then
  echo "ERROR: no product Floe configs found." >&2
  exit 1
fi

for config_path in "${configs[@]}"; do
  generate_manifest "${config_path}"
done
