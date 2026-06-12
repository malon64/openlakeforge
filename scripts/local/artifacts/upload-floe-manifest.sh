#!/usr/bin/env bash
# Publish generated product Floe manifests to the local code bucket.
set -euo pipefail

NAMESPACE="${NAMESPACE:-lakehouse}"
BUCKET="${CODE_BUCKET_NAME:-openlakeforge-code}"
S3_PORT="${SEAWEEDFS_LOCAL_S3_PORT:-19000}"

for cmd in kubectl base64; do
  if ! command -v "${cmd}" &>/dev/null; then
    echo "ERROR: '${cmd}' not found on PATH" >&2
    exit 1
  fi
done

if command -v aws &>/dev/null; then
  AWS_CMD=(aws)
elif command -v docker &>/dev/null; then
  AWS_CMD=(
    docker run --rm --network host
    -e AWS_ACCESS_KEY_ID
    -e AWS_SECRET_ACCESS_KEY
    -e AWS_REGION
    -e AWS_DEFAULT_REGION
    -e AWS_EC2_METADATA_DISABLED
    -v "${PWD}:/work"
    -w /work
    amazon/aws-cli:2.17.63
  )
else
  echo "ERROR: either 'aws' or Docker is required to upload Floe manifests." >&2
  exit 1
fi

secret_value() {
  local key="$1"
  kubectl get secret seaweedfs-s3-creds -n "${NAMESPACE}" \
    -o "jsonpath={.data.${key}}" | base64 -d
}

export AWS_ACCESS_KEY_ID
export AWS_SECRET_ACCESS_KEY
export AWS_REGION="${AWS_REGION:-us-east-1}"
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-${AWS_REGION}}"
export AWS_EC2_METADATA_DISABLED=true
AWS_ACCESS_KEY_ID="$(secret_value AWS_ACCESS_KEY_ID)"
AWS_SECRET_ACCESS_KEY="$(secret_value AWS_SECRET_ACCESS_KEY)"

discover_manifests() {
  if [[ -n "${FLOE_MANIFEST_PATH:-}" ]]; then
    printf '%s\n' "${FLOE_MANIFEST_PATH}"
    return
  fi

  find domains -path "*/contracts/floe/manifests/*.manifest.json" -type f | sort
}

manifest_key() {
  local manifest_path="$1"
  if [[ -n "${FLOE_MANIFEST_KEY:-}" ]]; then
    printf '%s\n' "${FLOE_MANIFEST_KEY}"
    return
  fi

  local domain_dir="${manifest_path%/contracts/floe/manifests/*}"
  local product
  local domain
  product="$(basename "${manifest_path}" .manifest.json)"
  domain="$(basename "${domain_dir}")"
  printf 'floe/%s/%s/%s.manifest.json\n' "${domain}" "${product}" "${product}"
}

mapfile -t manifests < <(discover_manifests)
if [[ "${#manifests[@]}" -eq 0 ]]; then
  echo "ERROR: no generated product Floe manifests found. Run 'make floe-manifest' first." >&2
  exit 1
fi

for manifest_path in "${manifests[@]}"; do
  if [[ ! -f "${manifest_path}" ]]; then
    echo "ERROR: missing Floe manifest at ${manifest_path}. Run 'make floe-manifest' first." >&2
    exit 1
  fi
done

kubectl port-forward "svc/seaweedfs-s3" "${S3_PORT}:8333" -n "${NAMESPACE}" >/tmp/openlakeforge-seaweedfs-port-forward.log 2>&1 &
port_forward_pid="$!"
cleanup() {
  kill "${port_forward_pid}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

endpoint="http://127.0.0.1:${S3_PORT}"
for attempt in $(seq 1 60); do
  if "${AWS_CMD[@]}" --endpoint-url "${endpoint}" s3api head-bucket --bucket "${BUCKET}" >/dev/null 2>&1; then
    break
  fi
  if [[ "${attempt}" == "60" ]]; then
    echo "ERROR: bucket '${BUCKET}' did not become available through ${endpoint}." >&2
    exit 1
  fi
  sleep 2
done

for manifest_path in "${manifests[@]}"; do
  key="$(manifest_key "${manifest_path}")"
  "${AWS_CMD[@]}" --endpoint-url "${endpoint}" s3api put-object \
    --bucket "${BUCKET}" \
    --key "${key}" \
    --body "${manifest_path}" \
    --content-type application/json

  echo "Published ${manifest_path} to s3://${BUCKET}/${key}"
done
