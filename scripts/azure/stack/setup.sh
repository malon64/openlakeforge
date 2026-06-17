#!/usr/bin/env bash
# Bring up the OpenLakeForge Azure POC stack in two explicit phases.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAMESPACE="${NAMESPACE:-lakehouse}"
AZURE_CLUSTER_NAME="${AZURE_CLUSTER_NAME:-aks-openlakeforge-poc}"
KUBE_CONTEXT="${KUBE_CONTEXT:-}"
PROJECT_CODE_IMAGE_PULL_POLICY="${PROJECT_CODE_IMAGE_PULL_POLICY:-Always}"
SUPERSET_IMAGE_PULL_POLICY="${SUPERSET_IMAGE_PULL_POLICY:-Always}"

echo "==> Phase 1/2: applying static Azure POC infrastructure..."
NAMESPACE="${NAMESPACE}" \
AZURE_CLUSTER_NAME="${AZURE_CLUSTER_NAME}" \
KUBE_CONTEXT="${KUBE_CONTEXT}" \
PROJECT_CODE_IMAGE_REPOSITORY="${PROJECT_CODE_IMAGE_REPOSITORY:-}" \
PROJECT_CODE_IMAGE_TAG="${PROJECT_CODE_IMAGE_TAG:-}" \
PROJECT_CODE_IMAGE_PULL_POLICY="${PROJECT_CODE_IMAGE_PULL_POLICY}" \
SUPERSET_IMAGE_REPOSITORY="${SUPERSET_IMAGE_REPOSITORY:-}" \
SUPERSET_IMAGE_TAG="${SUPERSET_IMAGE_TAG:-}" \
SUPERSET_IMAGE_PULL_POLICY="${SUPERSET_IMAGE_PULL_POLICY}" \
AZURE_IMAGE_TAG="${AZURE_IMAGE_TAG:-}" \
  bash "${SCRIPT_DIR}/infra-up.sh"

echo "==> Phase 2/2: deploying dynamic Azure POC artifacts..."
NAMESPACE="${NAMESPACE}" \
AZURE_CLUSTER_NAME="${AZURE_CLUSTER_NAME}" \
KUBE_CONTEXT="${KUBE_CONTEXT}" \
PROJECT_CODE_IMAGE_REPOSITORY="${PROJECT_CODE_IMAGE_REPOSITORY:-}" \
PROJECT_CODE_IMAGE_TAG="${PROJECT_CODE_IMAGE_TAG:-}" \
AZURE_IMAGE_TAG="${AZURE_IMAGE_TAG:-}" \
  bash "${SCRIPT_DIR}/deploy-artifacts.sh"

echo ""
echo "OpenLakeForge Azure POC stack is up."
echo ""
echo "Run 'make azure-forward' to port-forward all services, then open:"
echo ""
echo "  Dagster UI:       http://localhost:3000"
echo "  Superset UI:      http://localhost:8088  (admin / admin)"
echo "  OpenMetadata UI:  http://localhost:8585  (admin@open-metadata.org / admin)"
echo "  Trino UI:         http://localhost:8080"
echo "  Polaris API:      http://localhost:8181/api/catalog"
echo "  SeaweedFS S3:     http://localhost:9000"
