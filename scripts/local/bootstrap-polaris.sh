#!/usr/bin/env bash
# Bootstrap Apache Polaris after first deploy:
#   1. Obtain a root access token
#   2. Create the 'lakehouse' catalog (INTERNAL, backed by SeaweedFS S3)
#   3. Create a 'trino' service principal
#   4. Wire up roles and grant CATALOG_MANAGE_CONTENT to trino
#   5. Persist the Trino principal credentials in Kubernetes Secret polaris-trino-creds
#
# Exports: POLARIS_TRINO_CLIENT_ID, POLARIS_TRINO_CLIENT_SECRET
# Requires: kubectl port-forward to polaris:8181 is running (set up by setup.sh)
# Safe to re-run — skips objects that already exist (409 responses are ignored).
set -euo pipefail

NAMESPACE="${NAMESPACE:-lakehouse}"
POLARIS_HOST="${POLARIS_HOST:-localhost:8181}"
BUCKET_NAME="${BUCKET_NAME:-iceberg-data}"
CATALOG_NAME="lakehouse"
PRINCIPAL_NAME="trino"
PRINCIPAL_ROLE="data-engineer"
CATALOG_ROLE="catalog-admin"

ROOT_CLIENT_ID="root"
ROOT_CLIENT_SECRET="polaris-secret"

polaris_curl() {
  local method="$1"; shift
  local path="$1"; shift
  curl -sf -X "${method}" "http://${POLARIS_HOST}${path}" "$@"
}

polaris_mgmt() {
  local method="$1"; shift
  local path="$1"; shift
  polaris_curl "${method}" "/api/management/v1${path}" \
    -H "Authorization: Bearer ${POLARIS_TOKEN}" \
    -H "Content-Type: application/json" \
    "$@"
}

polaris_mgmt_request() {
  local method="$1"; shift
  local path="$1"; shift
  local expected_codes="$1"; shift
  local body_file http_code

  body_file="$(mktemp)"
  http_code=$(curl -sS -o "${body_file}" -w "%{http_code}" \
    -X "${method}" "http://${POLARIS_HOST}/api/management/v1${path}" \
    -H "Authorization: Bearer ${POLARIS_TOKEN}" \
    -H "Content-Type: application/json" \
    "$@")

  POLARIS_RESPONSE_BODY="$(cat "${body_file}")"
  rm -f "${body_file}"

  if [[ " ${expected_codes} " == *" ${http_code} "* ]]; then
    return 0
  fi

  echo "ERROR: Polaris ${method} ${path} returned HTTP ${http_code}" >&2
  [[ -z "${POLARIS_RESPONSE_BODY}" ]] || echo "${POLARIS_RESPONSE_BODY}" >&2
  return 1
}

read_existing_trino_creds() {
  if kubectl get secret polaris-trino-creds -n "${NAMESPACE}" &>/dev/null; then
    POLARIS_TRINO_CLIENT_ID=$(kubectl get secret polaris-trino-creds -n "${NAMESPACE}" \
      -o jsonpath='{.data.CLIENT_ID}' | base64 -d)
    POLARIS_TRINO_CLIENT_SECRET=$(kubectl get secret polaris-trino-creds -n "${NAMESPACE}" \
      -o jsonpath='{.data.CLIENT_SECRET}' | base64 -d)

    if polaris_curl POST "/api/catalog/v1/oauth/tokens" \
      -u "${POLARIS_TRINO_CLIENT_ID}:${POLARIS_TRINO_CLIENT_SECRET}" \
      -d "grant_type=client_credentials" \
      -d "scope=PRINCIPAL_ROLE:ALL" &>/dev/null; then
      return 0
    fi

    echo "    Existing Secret 'polaris-trino-creds' is stale — recreating principal."
    kubectl delete secret polaris-trino-creds --namespace "${NAMESPACE}" --ignore-not-found
  fi

  return 1
}

create_trino_principal() {
  polaris_mgmt_request POST "/principals" "201" \
    -d "{\"name\": \"${PRINCIPAL_NAME}\", \"type\": \"SERVICE\"}"

  POLARIS_TRINO_CLIENT_ID=$(echo "${POLARIS_RESPONSE_BODY}" \
    | grep -o '"clientId":"[^"]*"' | head -n 1 | cut -d'"' -f4 || true)
  POLARIS_TRINO_CLIENT_SECRET=$(echo "${POLARIS_RESPONSE_BODY}" \
    | grep -o '"clientSecret":"[^"]*"' | head -n 1 | cut -d'"' -f4 || true)

  if [[ -z "${POLARIS_TRINO_CLIENT_ID}" || -z "${POLARIS_TRINO_CLIENT_SECRET}" ]]; then
    echo "ERROR: Polaris did not return credentials for principal '${PRINCIPAL_NAME}'." >&2
    echo "${POLARIS_RESPONSE_BODY}" >&2
    exit 1
  fi
}

echo "==> Obtaining Polaris root token..."
TOKEN_RESPONSE=$(polaris_curl POST "/api/catalog/v1/oauth/tokens" \
  -u "${ROOT_CLIENT_ID}:${ROOT_CLIENT_SECRET}" \
  -d "grant_type=client_credentials" \
  -d "scope=PRINCIPAL_ROLE:ALL")
POLARIS_TOKEN=$(echo "${TOKEN_RESPONSE}" | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4 || true)

if [[ -z "${POLARIS_TOKEN}" ]]; then
  echo "ERROR: failed to obtain Polaris token. Response:"
  echo "${TOKEN_RESPONSE}"
  exit 1
fi
echo "    Token obtained."

echo "==> Creating catalog '${CATALOG_NAME}'..."
polaris_mgmt_request POST "/catalogs" "201 409" -d "{
  \"name\": \"${CATALOG_NAME}\",
  \"type\": \"INTERNAL\",
  \"properties\": {
    \"default-base-location\": \"s3://${BUCKET_NAME}\"
  },
  \"storageConfigInfo\": {
    \"storageType\": \"S3\",
    \"allowedLocations\": [\"s3://${BUCKET_NAME}/\"],
    \"pathStyleAccess\": true,
    \"stsUnavailable\": true
  }
}"
echo "    Done."

echo "==> Ensuring principal '${PRINCIPAL_NAME}' credentials..."
if read_existing_trino_creds; then
  echo "    Reusing credentials from Secret 'polaris-trino-creds'."
else
  if polaris_mgmt_request GET "/principals/${PRINCIPAL_NAME}" "200 404"; then
    if [[ "${POLARIS_RESPONSE_BODY}" == *"\"name\":\"${PRINCIPAL_NAME}\""* ]]; then
      echo "    Principal exists but credentials were not persisted — recreating it."
      polaris_mgmt_request DELETE "/principals/${PRINCIPAL_NAME}" "204"
    fi
  fi

  create_trino_principal

  echo "==> Writing Kubernetes Secret 'polaris-trino-creds'..."
  kubectl delete secret polaris-trino-creds --namespace "${NAMESPACE}" --ignore-not-found
  kubectl create secret generic polaris-trino-creds \
    --namespace "${NAMESPACE}" \
    --from-literal=CLIENT_ID="${POLARIS_TRINO_CLIENT_ID}" \
    --from-literal=CLIENT_SECRET="${POLARIS_TRINO_CLIENT_SECRET}"
fi
echo "    Client ID: ${POLARIS_TRINO_CLIENT_ID}"

echo "==> Creating principal role '${PRINCIPAL_ROLE}'..."
polaris_mgmt_request POST "/principal-roles" "201 409" -d \
  "{\"principalRole\": {\"name\": \"${PRINCIPAL_ROLE}\"}}"

echo "==> Assigning principal role to '${PRINCIPAL_NAME}'..."
polaris_mgmt_request PUT "/principals/${PRINCIPAL_NAME}/principal-roles" "201 409" -d \
  "{\"principalRole\": {\"name\": \"${PRINCIPAL_ROLE}\"}}"

echo "==> Creating catalog role '${CATALOG_ROLE}' on '${CATALOG_NAME}'..."
polaris_mgmt_request POST "/catalogs/${CATALOG_NAME}/catalog-roles" "201 409" -d \
  "{\"catalogRole\": {\"name\": \"${CATALOG_ROLE}\"}}"

echo "==> Granting CATALOG_MANAGE_CONTENT to catalog role..."
polaris_mgmt_request PUT \
  "/catalogs/${CATALOG_NAME}/catalog-roles/${CATALOG_ROLE}/grants" "201 409" -d \
  "{\"grant\": {\"type\": \"catalog\", \"privilege\": \"CATALOG_MANAGE_CONTENT\"}}"

echo "==> Assigning catalog role to principal role..."
polaris_mgmt_request PUT \
  "/principal-roles/${PRINCIPAL_ROLE}/catalog-roles/${CATALOG_NAME}" "201 409" -d \
  "{\"catalogRole\": {\"name\": \"${CATALOG_ROLE}\"}}"

export POLARIS_TRINO_CLIENT_ID
export POLARIS_TRINO_CLIENT_SECRET

echo ""
echo "Polaris bootstrap complete."
echo "  Trino client ID:     ${POLARIS_TRINO_CLIENT_ID}"
echo "  Trino client secret: ${POLARIS_TRINO_CLIENT_SECRET}"
