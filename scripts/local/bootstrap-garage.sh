#!/usr/bin/env bash
# Bootstrap a single-node Garage instance:
#   1. Apply storage layout (capacity + zone)
#   2. Create the iceberg-data S3 bucket
#   3. Create an access key and grant it ownership of the bucket
#   4. Write credentials to a Kubernetes Secret (garage-s3-creds)
#
# Exports: GARAGE_KEY_ID, GARAGE_SECRET_KEY
# Safe to re-run — skips steps that are already done.
set -euo pipefail

NAMESPACE="${NAMESPACE:-lakehouse}"
BUCKET_NAME="iceberg-data"
KEY_NAME="polaris-trino-key"

GARAGE_POD=$(kubectl get pods -n "${NAMESPACE}" \
  -l "app.kubernetes.io/instance=garage" \
  -o jsonpath='{.items[0].metadata.name}')

garage_exec() {
  kubectl exec -n "${NAMESPACE}" "${GARAGE_POD}" -- garage "$@"
}

echo "==> Checking Garage layout..."
UNASSIGNED=$(garage_exec layout show 2>/dev/null | grep -c "NO ROLE" || true)
if [[ "${UNASSIGNED}" -gt 0 ]]; then
  NODE_ID=$(garage_exec layout show | awk '/NO ROLE/{print $1; exit}')
  echo "    Assigning node ${NODE_ID} to zone dc1 with 1G capacity"
  garage_exec layout assign -z dc1 -c 1G "${NODE_ID}"
  garage_exec layout apply --version 1
  echo "    Layout applied."
else
  echo "    Layout already configured, skipping."
fi

echo "==> Creating bucket '${BUCKET_NAME}'..."
if garage_exec bucket list | grep -qF "${BUCKET_NAME}"; then
  echo "    Bucket already exists, skipping."
else
  garage_exec bucket create "${BUCKET_NAME}"
  echo "    Bucket created."
fi

echo "==> Creating access key '${KEY_NAME}'..."
if garage_exec key list | grep -qF "${KEY_NAME}"; then
  echo "    Key already exists — fetching credentials with --show-secret..."
  KEY_OUTPUT=$(garage_exec key get --show-secret "${KEY_NAME}")
else
  KEY_OUTPUT=$(garage_exec key create "${KEY_NAME}")
fi

GARAGE_KEY_ID=$(echo "${KEY_OUTPUT}"    | grep -i "Key ID"     | awk '{print $NF}')
GARAGE_SECRET_KEY=$(echo "${KEY_OUTPUT}" | grep -i "Secret key" | awk '{print $NF}')

if [[ -z "${GARAGE_KEY_ID}" || -z "${GARAGE_SECRET_KEY}" ]]; then
  echo "ERROR: could not parse key credentials from garage output:"
  echo "${KEY_OUTPUT}"
  exit 1
fi

echo "==> Granting key access to bucket '${BUCKET_NAME}'..."
garage_exec bucket allow --read --write --owner "${BUCKET_NAME}" --key "${KEY_NAME}"

echo "==> Writing Kubernetes Secret 'garage-s3-creds'..."
kubectl create secret generic garage-s3-creds \
  --namespace "${NAMESPACE}" \
  --from-literal=AWS_ACCESS_KEY_ID="${GARAGE_KEY_ID}" \
  --from-literal=AWS_SECRET_ACCESS_KEY="${GARAGE_SECRET_KEY}" \
  --dry-run=client -o yaml | kubectl apply -f -

export GARAGE_KEY_ID
export GARAGE_SECRET_KEY

echo ""
echo "Garage bootstrap complete."
echo "  Key ID:     ${GARAGE_KEY_ID}"
echo "  Secret Key: ${GARAGE_SECRET_KEY}"
