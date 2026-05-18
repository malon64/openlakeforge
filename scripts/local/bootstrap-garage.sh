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
  kubectl exec -n "${NAMESPACE}" "${GARAGE_POD}" -- /garage -c /etc/garage/garage.toml "$@"
}

echo "==> Applying Garage layout..."
# Extract the node ID from 'garage status' — first hex token on a data line.
# This is format-agnostic across Garage v0.9 and v1.x output styles.
NODE_ID=$(garage_exec status 2>&1 | awk '/^[0-9a-f]/{print $1; exit}')
if [[ -z "${NODE_ID}" ]]; then
  echo "ERROR: could not determine node ID from 'garage status'" >&2
  garage_exec status 2>&1 >&2
  exit 1
fi
echo "    Node ID: ${NODE_ID}"

# Assign zone + capacity (idempotent — safe to repeat).
garage_exec layout assign -z dc1 -c 1G "${NODE_ID}"

# Apply as version 1. If version 1 is already live this is a no-op error — ignore it.
garage_exec layout apply --version 1 2>/dev/null \
  || echo "    Layout version 1 already applied, continuing."

echo "==> Creating bucket '${BUCKET_NAME}'..."
if garage_exec bucket list | grep -qF "${BUCKET_NAME}"; then
  echo "    Bucket already exists, skipping."
else
  garage_exec bucket create "${BUCKET_NAME}"
  echo "    Bucket created."
fi

echo "==> Creating access key '${KEY_NAME}'..."
if garage_exec key list 2>&1 | grep -qF "${KEY_NAME}"; then
  # Garage v1.x does not allow re-reading secrets after creation.
  # If the key already exists, recover credentials from the k8s Secret
  # written on the previous run. If that Secret is also gone, recreate the key.
  if kubectl get secret garage-s3-creds -n "${NAMESPACE}" &>/dev/null; then
    echo "    Key exists — reading credentials from existing Secret..."
    GARAGE_KEY_ID=$(kubectl get secret garage-s3-creds -n "${NAMESPACE}" \
      -o jsonpath='{.data.AWS_ACCESS_KEY_ID}' | base64 -d)
    GARAGE_SECRET_KEY=$(kubectl get secret garage-s3-creds -n "${NAMESPACE}" \
      -o jsonpath='{.data.AWS_SECRET_ACCESS_KEY}' | base64 -d)
  else
    echo "    Key exists but Secret is gone — deleting key and recreating..."
    garage_exec key delete "${KEY_NAME}" 2>/dev/null || true
    KEY_OUTPUT=$(garage_exec key create "${KEY_NAME}")
    GARAGE_KEY_ID=$(echo "${KEY_OUTPUT}"     | grep -i "Key ID"     | awk '{print $NF}')
    GARAGE_SECRET_KEY=$(echo "${KEY_OUTPUT}" | grep -i "Secret key" | awk '{print $NF}')
  fi
else
  KEY_OUTPUT=$(garage_exec key create "${KEY_NAME}")
  GARAGE_KEY_ID=$(echo "${KEY_OUTPUT}"     | grep -i "Key ID"     | awk '{print $NF}')
  GARAGE_SECRET_KEY=$(echo "${KEY_OUTPUT}" | grep -i "Secret key" | awk '{print $NF}')
fi

if [[ -z "${GARAGE_KEY_ID}" || -z "${GARAGE_SECRET_KEY}" ]]; then
  echo "ERROR: could not obtain key credentials." >&2
  exit 1
fi

echo "==> Granting key access to bucket '${BUCKET_NAME}'..."
garage_exec bucket allow --read --write --owner "${BUCKET_NAME}" --key "${KEY_NAME}"

echo "==> Writing Kubernetes Secret 'garage-s3-creds'..."
kubectl delete secret garage-s3-creds --namespace "${NAMESPACE}" --ignore-not-found
kubectl create secret generic garage-s3-creds \
  --namespace "${NAMESPACE}" \
  --from-literal=AWS_ACCESS_KEY_ID="${GARAGE_KEY_ID}" \
  --from-literal=AWS_SECRET_ACCESS_KEY="${GARAGE_SECRET_KEY}"

export GARAGE_KEY_ID
export GARAGE_SECRET_KEY

echo ""
echo "Garage bootstrap complete."
echo "  Key ID:     ${GARAGE_KEY_ID}"
echo "  Secret Key: ${GARAGE_SECRET_KEY}"
