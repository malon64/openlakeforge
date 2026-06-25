#!/usr/bin/env bash
# Bring up the OpenLakeForge AWS POC stack in two explicit phases.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAMESPACE="${NAMESPACE:-lakehouse}"
AWS_CLUSTER_NAME="${AWS_CLUSTER_NAME:-eks-openlakeforge-poc}"
KUBE_CONTEXT="${KUBE_CONTEXT:-}"
PROJECT_CODE_IMAGE_PULL_POLICY="${PROJECT_CODE_IMAGE_PULL_POLICY:-Always}"
SUPERSET_IMAGE_PULL_POLICY="${SUPERSET_IMAGE_PULL_POLICY:-Always}"

echo "==> Phase 1/2: applying static AWS POC infrastructure..."
NAMESPACE="${NAMESPACE}" \
AWS_CLUSTER_NAME="${AWS_CLUSTER_NAME}" \
KUBE_CONTEXT="${KUBE_CONTEXT}" \
PROJECT_CODE_IMAGE_REPOSITORY="${PROJECT_CODE_IMAGE_REPOSITORY:-}" \
PROJECT_CODE_IMAGE_TAG="${PROJECT_CODE_IMAGE_TAG:-}" \
PROJECT_CODE_IMAGE_PULL_POLICY="${PROJECT_CODE_IMAGE_PULL_POLICY}" \
SUPERSET_IMAGE_REPOSITORY="${SUPERSET_IMAGE_REPOSITORY:-}" \
SUPERSET_IMAGE_TAG="${SUPERSET_IMAGE_TAG:-}" \
SUPERSET_IMAGE_PULL_POLICY="${SUPERSET_IMAGE_PULL_POLICY}" \
AWS_IMAGE_TAG="${AWS_IMAGE_TAG:-}" \
  bash "${SCRIPT_DIR}/infra-up.sh"

echo "==> Phase 2/2: deploying dynamic AWS POC artifacts..."
NAMESPACE="${NAMESPACE}" \
AWS_CLUSTER_NAME="${AWS_CLUSTER_NAME}" \
KUBE_CONTEXT="${KUBE_CONTEXT}" \
PROJECT_CODE_IMAGE_REPOSITORY="${PROJECT_CODE_IMAGE_REPOSITORY:-}" \
PROJECT_CODE_IMAGE_TAG="${PROJECT_CODE_IMAGE_TAG:-}" \
AWS_IMAGE_TAG="${AWS_IMAGE_TAG:-}" \
  bash "${SCRIPT_DIR}/deploy-artifacts.sh"

echo ""
echo "OpenLakeForge AWS POC stack is up."
echo ""
echo "Run 'make aws-forward' to port-forward services, then open:"
echo ""
echo "  Dagster UI:       http://localhost:3000"
echo "  Superset UI:      http://localhost:8088  (admin / admin)"
echo "  OpenMetadata UI:  http://localhost:8585  (admin@open-metadata.org / admin)"
echo "  Trino UI:         http://localhost:8080"
