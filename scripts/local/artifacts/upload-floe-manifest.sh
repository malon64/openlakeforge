#!/usr/bin/env bash
# Publish the generated Sales Floe manifest to the local code bucket.
set -euo pipefail

NAMESPACE="${NAMESPACE:-lakehouse}"
BUCKET="${CODE_BUCKET_NAME:-openlakeforge-code}"
MANIFEST_PATH="${FLOE_MANIFEST_PATH:-domains/sales/contracts/floe/manifests/sales.manifest.json}"
MANIFEST_KEY="${FLOE_MANIFEST_KEY:-floe/sales/sales.manifest.json}"
S3_PORT="${SEAWEEDFS_LOCAL_S3_PORT:-19000}"

if [[ ! -f "${MANIFEST_PATH}" ]]; then
  echo "ERROR: missing Floe manifest at ${MANIFEST_PATH}. Run 'make floe-manifest' first." >&2
  exit 1
fi

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
  echo "ERROR: either 'aws' or Docker is required to upload the Floe manifest." >&2
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

"${AWS_CMD[@]}" --endpoint-url "${endpoint}" s3api put-object \
  --bucket "${BUCKET}" \
  --key "${MANIFEST_KEY}" \
  --body "${MANIFEST_PATH}" \
  --content-type application/json

echo "Published ${MANIFEST_PATH} to s3://${BUCKET}/${MANIFEST_KEY}"
