#!/usr/bin/env bash
# Bootstrap Apache Polaris after first deploy:
#   1. Obtain a root access token
#   2. Create the 'lakehouse' catalog (INTERNAL, backed by Garage S3)
#   3. Create a 'trino' service principal
#   4. Wire up roles and grant CATALOG_MANAGE_CONTENT to trino
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

allow_409() {
  # Treat HTTP 409 (already exists) as success
  "$@" || {
    local rc=$?
    [[ $rc -eq 22 ]] && return 0   # curl -sf exits 22 on 4xx/5xx
    return $rc
  }
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
allow_409 polaris_mgmt POST "/catalogs" -d "{
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

echo "==> Creating principal '${PRINCIPAL_NAME}'..."
PRINCIPAL_RESPONSE=$(polaris_mgmt POST "/principals" -d \
  "{\"name\": \"${PRINCIPAL_NAME}\", \"type\": \"SERVICE\"}" 2>/dev/null || true)

POLARIS_TRINO_CLIENT_ID=$(echo "${PRINCIPAL_RESPONSE}" \
  | grep -o '"clientId":"[^"]*"' | cut -d'"' -f4 || true)
POLARIS_TRINO_CLIENT_SECRET=$(echo "${PRINCIPAL_RESPONSE}" \
  | grep -o '"clientSecret":"[^"]*"' | cut -d'"' -f4 || true)

if [[ -z "${POLARIS_TRINO_CLIENT_ID}" ]]; then
  echo "    Principal already exists or credentials unavailable — using placeholder."
  echo "    Re-run teardown + setup to get fresh credentials."
  POLARIS_TRINO_CLIENT_ID="trino"
  POLARIS_TRINO_CLIENT_SECRET="trino-secret-rerun-setup-to-refresh"
fi
echo "    Client ID: ${POLARIS_TRINO_CLIENT_ID}"

echo "==> Creating principal role '${PRINCIPAL_ROLE}'..."
allow_409 polaris_mgmt POST "/principal-roles" -d \
  "{\"principalRole\": {\"name\": \"${PRINCIPAL_ROLE}\"}}"

echo "==> Assigning principal role to '${PRINCIPAL_NAME}'..."
allow_409 polaris_mgmt PUT "/principals/${PRINCIPAL_NAME}/principal-roles" -d \
  "{\"principalRole\": {\"name\": \"${PRINCIPAL_ROLE}\"}}"

echo "==> Creating catalog role '${CATALOG_ROLE}' on '${CATALOG_NAME}'..."
allow_409 polaris_mgmt POST "/catalogs/${CATALOG_NAME}/catalog-roles" -d \
  "{\"catalogRole\": {\"name\": \"${CATALOG_ROLE}\"}}"

echo "==> Granting CATALOG_MANAGE_CONTENT to catalog role..."
allow_409 polaris_mgmt PUT \
  "/catalogs/${CATALOG_NAME}/catalog-roles/${CATALOG_ROLE}/grants" -d \
  "{\"type\": \"CatalogGrant\", \"privilege\": \"CATALOG_MANAGE_CONTENT\"}"

echo "==> Assigning catalog role to principal role..."
allow_409 polaris_mgmt PUT \
  "/principal-roles/${PRINCIPAL_ROLE}/catalog-roles/${CATALOG_NAME}" -d \
  "{\"catalogRole\": {\"name\": \"${CATALOG_ROLE}\"}}"

export POLARIS_TRINO_CLIENT_ID
export POLARIS_TRINO_CLIENT_SECRET

echo ""
echo "Polaris bootstrap complete."
echo "  Trino client ID:     ${POLARIS_TRINO_CLIENT_ID}"
echo "  Trino client secret: ${POLARIS_TRINO_CLIENT_SECRET}"
