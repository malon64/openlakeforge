#!/usr/bin/env bash
# Bootstrap the local SeaweedFS S3 endpoint:
#   1. Wait for the S3 service to answer.
#   2. Ensure the Iceberg bucket exists.
#   3. Write credentials to Kubernetes Secret seaweedfs-s3-creds.
#
# Exports: SEAWEEDFS_ACCESS_KEY, SEAWEEDFS_SECRET_KEY
set -euo pipefail

NAMESPACE="${NAMESPACE:-lakehouse}"
BUCKET_NAME="${BUCKET_NAME:-iceberg-data}"
SEAWEEDFS_ACCESS_KEY="${SEAWEEDFS_ACCESS_KEY:-openlakeforge}"
SEAWEEDFS_SECRET_KEY="${SEAWEEDFS_SECRET_KEY:-openlakeforge-secret}"
SEAWEEDFS_REGION="${SEAWEEDFS_REGION:-us-east-1}"
SEAWEEDFS_LOCAL_ENDPOINT="${SEAWEEDFS_LOCAL_ENDPOINT:-http://localhost:9000}"

seaweedfs_port_forward_bg() {
  kubectl port-forward svc/seaweedfs-s3 9000:8333 -n "${NAMESPACE}" &>/dev/null &
  echo $!
}

seaweedfs_aws_s3() {
  AWS_ACCESS_KEY_ID="${SEAWEEDFS_ACCESS_KEY}" \
    AWS_SECRET_ACCESS_KEY="${SEAWEEDFS_SECRET_KEY}" \
    AWS_DEFAULT_REGION="${SEAWEEDFS_REGION}" \
    aws --endpoint-url "${SEAWEEDFS_LOCAL_ENDPOINT}" s3 "$@"
}

echo "==> Waiting for SeaweedFS S3 service..."
kubectl rollout status deployment/seaweedfs-s3 -n "${NAMESPACE}" --timeout=300s

PF_PID=$(seaweedfs_port_forward_bg)
trap 'kill "${PF_PID}" 2>/dev/null || true' EXIT
sleep 3

echo "==> Ensuring bucket '${BUCKET_NAME}' exists..."
if seaweedfs_aws_s3 ls "s3://${BUCKET_NAME}" &>/dev/null; then
  echo "    Bucket already exists, skipping."
else
  seaweedfs_aws_s3 mb "s3://${BUCKET_NAME}"
fi

echo "==> Writing Kubernetes Secret 'seaweedfs-s3-creds'..."
kubectl delete secret seaweedfs-s3-creds --namespace "${NAMESPACE}" --ignore-not-found
kubectl create secret generic seaweedfs-s3-creds \
  --namespace "${NAMESPACE}" \
  --from-literal=AWS_ACCESS_KEY_ID="${SEAWEEDFS_ACCESS_KEY}" \
  --from-literal=AWS_SECRET_ACCESS_KEY="${SEAWEEDFS_SECRET_KEY}"

export SEAWEEDFS_ACCESS_KEY
export SEAWEEDFS_SECRET_KEY

kill "${PF_PID}" 2>/dev/null || true
trap - EXIT

echo ""
echo "SeaweedFS bootstrap complete."
echo "  Access key: ${SEAWEEDFS_ACCESS_KEY}"
