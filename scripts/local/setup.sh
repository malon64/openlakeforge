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
# Write via printf to avoid CRLF from heredoc on Windows-hosted filesystems.
# additionalCatalogs is the established key in trinodb/charts.
printf 'additionalCatalogs:\n' > "${GENERATED_VALUES}"
printf '  iceberg: |\n' >> "${GENERATED_VALUES}"
printf '    connector.name=iceberg\n' >> "${GENERATED_VALUES}"
printf '    iceberg.catalog.type=rest\n' >> "${GENERATED_VALUES}"
printf '    iceberg.rest-catalog.uri=http://polaris:8181/api/catalog\n' >> "${GENERATED_VALUES}"
printf '    iceberg.rest-catalog.warehouse=%s\n' "${CATALOG_NAME:-lakehouse}" >> "${GENERATED_VALUES}"
printf '    iceberg.rest-catalog.security=OAUTH2\n' >> "${GENERATED_VALUES}"
printf '    iceberg.rest-catalog.oauth2.credential=%s:%s\n' "${POLARIS_TRINO_CLIENT_ID}" "${POLARIS_TRINO_CLIENT_SECRET}" >> "${GENERATED_VALUES}"
printf '    iceberg.rest-catalog.oauth2.server-uri=http://polaris:8181/api/catalog/v1/oauth/tokens\n' >> "${GENERATED_VALUES}"
printf '    iceberg.rest-catalog.oauth2.scope=PRINCIPAL_ROLE:ALL\n' >> "${GENERATED_VALUES}"
printf '    iceberg.rest-catalog.vended-credentials-enabled=true\n' >> "${GENERATED_VALUES}"
printf '    iceberg.rest-catalog.nested-namespace-enabled=true\n' >> "${GENERATED_VALUES}"
printf '    fs.native-s3.enabled=true\n' >> "${GENERATED_VALUES}"
printf '    s3.endpoint=http://garage:3900\n' >> "${GENERATED_VALUES}"
printf '    s3.path-style-access=true\n' >> "${GENERATED_VALUES}"
printf '    s3.region=us-east-1\n' >> "${GENERATED_VALUES}"
printf '    s3.aws-access-key=%s\n' "${GARAGE_KEY_ID}" >> "${GENERATED_VALUES}"
printf '    s3.aws-secret-key=%s\n' "${GARAGE_SECRET_KEY}" >> "${GENERATED_VALUES}"

echo "    Written to ${GENERATED_VALUES}:"
cat "${GENERATED_VALUES}"

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
