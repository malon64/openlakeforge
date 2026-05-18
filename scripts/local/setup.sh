#!/usr/bin/env bash
# Bring up the OpenLakeForge local stack on an existing Kubernetes cluster.
#
# Prerequisites (must be on PATH):
#   kubectl, helm, curl
#
# The script assumes kubectl is already pointing at the target cluster.
# On Windows run this from Git Bash (comes with Git for Windows).
#
# Usage:
#   bash scripts/local/setup.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
HELM_VALUES="${REPO_ROOT}/infra/helm/values"
GENERATED_VALUES="/tmp/trino-iceberg-generated.yaml"

export NAMESPACE="${NAMESPACE:-lakehouse}"
export BUCKET_NAME="iceberg-data"

check_prereqs() {
  local missing=0
  for cmd in kubectl helm curl; do
    if ! command -v "${cmd}" &>/dev/null; then
      echo "ERROR: '${cmd}' not found on PATH" >&2
      missing=1
    fi
  done
  [[ $missing -eq 0 ]] || exit 1
}

wait_rollout() {
  local kind="$1" name="$2"
  echo "    Waiting for ${kind}/${name}..."
  kubectl rollout status "${kind}/${name}" -n "${NAMESPACE}" --timeout=300s
}

port_forward_bg() {
  local svc="$1" local_port="$2" remote_port="$3"
  kubectl port-forward "svc/${svc}" "${local_port}:${remote_port}" \
    -n "${NAMESPACE}" &>/dev/null &
  echo $!
}

# ── Step 0: prerequisites ──────────────────────────────────────────────────
echo "==> Checking prerequisites..."
check_prereqs

# ── Step 1: namespace ─────────────────────────────────────────────────────
echo "==> Creating namespace '${NAMESPACE}'..."
kubectl get namespace "${NAMESPACE}" &>/dev/null || kubectl create namespace "${NAMESPACE}"

# ── Step 2: Helm repos ────────────────────────────────────────────────────
echo "==> Adding Helm repos..."
helm repo add polaris https://downloads.apache.org/polaris/helm-chart 2>/dev/null || true
helm repo add trino   https://trinodb.github.io/charts               2>/dev/null || true
helm repo update

# ── Step 3: Garage ────────────────────────────────────────────────────────
echo "==> Installing Garage..."
helm upgrade --install garage "${REPO_ROOT}/infra/helm/charts/garage" \
  --namespace "${NAMESPACE}" \
  -f "${HELM_VALUES}/garage.yaml" \
  --wait --timeout 5m

wait_rollout statefulset garage

# ── Step 4: Bootstrap Garage (layout + bucket + key) ─────────────────────
echo "==> Bootstrapping Garage..."
# shellcheck source=./bootstrap-garage.sh
source "${SCRIPT_DIR}/bootstrap-garage.sh"
# GARAGE_KEY_ID and GARAGE_SECRET_KEY are now exported

# ── Step 5: Polaris ───────────────────────────────────────────────────────
echo "==> Installing Polaris..."
helm upgrade --install polaris polaris/polaris \
  --namespace "${NAMESPACE}" \
  -f "${HELM_VALUES}/polaris.yaml" \
  --wait --timeout 5m

wait_rollout deployment polaris

# ── Step 6: Bootstrap Polaris (catalog + principal + grants) ──────────────
echo "==> Starting port-forward for Polaris bootstrap..."
PF_PID=$(port_forward_bg polaris 8181 8181)
trap 'kill "${PF_PID}" 2>/dev/null || true' EXIT

# Give port-forward a moment to establish
sleep 3

export POLARIS_HOST="localhost:8181"
# shellcheck source=./bootstrap-polaris.sh
source "${SCRIPT_DIR}/bootstrap-polaris.sh"
# POLARIS_TRINO_CLIENT_ID and POLARIS_TRINO_CLIENT_SECRET are now exported

kill "${PF_PID}" 2>/dev/null || true

# ── Step 7: Generate Trino catalog values with real credentials ───────────
echo "==> Generating Trino iceberg catalog values..."
cat > "${GENERATED_VALUES}" <<EOF
catalogs:
  iceberg: |
    connector.name=iceberg
    iceberg.catalog.type=rest
    iceberg.rest-catalog.uri=http://polaris:8181/api/catalog
    iceberg.rest-catalog.warehouse=${CATALOG_NAME:-lakehouse}
    iceberg.rest-catalog.security=OAUTH2
    iceberg.rest-catalog.oauth2.credential=${POLARIS_TRINO_CLIENT_ID}:${POLARIS_TRINO_CLIENT_SECRET}
    iceberg.rest-catalog.oauth2.server-uri=http://polaris:8181/api/catalog/v1/oauth/tokens
    iceberg.rest-catalog.oauth2.scope=PRINCIPAL_ROLE:ALL
    iceberg.rest-catalog.vended-credentials-enabled=true
    iceberg.rest-catalog.nested-namespace-enabled=true
    fs.native-s3.enabled=true
    s3.endpoint=http://garage:3900
    s3.path-style-access=true
    s3.region=us-east-1
    s3.aws-access-key=${GARAGE_KEY_ID}
    s3.aws-secret-key=${GARAGE_SECRET_KEY}
EOF
echo "    Written to ${GENERATED_VALUES} (not committed to git)"

# ── Step 8: Trino ─────────────────────────────────────────────────────────
echo "==> Installing Trino..."
helm upgrade --install trino trino/trino \
  --namespace "${NAMESPACE}" \
  -f "${HELM_VALUES}/trino.yaml" \
  -f "${GENERATED_VALUES}" \
  --wait --timeout 5m

# ── Done ──────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  OpenLakeForge local stack is up                            ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Port-forward commands:                                     ║"
echo "║    kubectl port-forward svc/garage  9000:3900 -n lakehouse  ║"
echo "║    kubectl port-forward svc/polaris 8181:8181 -n lakehouse  ║"
echo "║    kubectl port-forward svc/trino   8080:8080 -n lakehouse  ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Trino UI:    http://localhost:8080  (user: any, no pwd)    ║"
echo "║  Polaris API: http://localhost:8181/api/catalog             ║"
echo "║  Garage S3:   http://localhost:9000                         ║"
echo "╚══════════════════════════════════════════════════════════════╝"
