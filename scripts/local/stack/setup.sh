#!/usr/bin/env bash
# Bring up the OpenLakeForge local stack as two explicit phases:
# static Terraform infrastructure, then dynamic local/CD artifacts.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAMESPACE="${NAMESPACE:-lakehouse}"
PROJECT_CODE_IMAGE_REPOSITORY="${PROJECT_CODE_IMAGE_REPOSITORY:-ghcr.io/openlakeforge/project-code}"
PROJECT_CODE_IMAGE_TAG="${PROJECT_CODE_IMAGE_TAG:-local}"
PROJECT_CODE_IMAGE_PULL_POLICY="${PROJECT_CODE_IMAGE_PULL_POLICY:-Never}"
SUPERSET_IMAGE_REPOSITORY="${SUPERSET_IMAGE_REPOSITORY:-ghcr.io/openlakeforge/superset}"
SUPERSET_IMAGE_TAG="${SUPERSET_IMAGE_TAG:-local}"
SUPERSET_IMAGE_PULL_POLICY="${SUPERSET_IMAGE_PULL_POLICY:-Never}"

echo "==> Phase 1/2: applying static local infrastructure..."
NAMESPACE="${NAMESPACE}" \
PROJECT_CODE_IMAGE_REPOSITORY="${PROJECT_CODE_IMAGE_REPOSITORY}" \
PROJECT_CODE_IMAGE_TAG="${PROJECT_CODE_IMAGE_TAG}" \
PROJECT_CODE_IMAGE_PULL_POLICY="${PROJECT_CODE_IMAGE_PULL_POLICY}" \
SUPERSET_IMAGE_REPOSITORY="${SUPERSET_IMAGE_REPOSITORY}" \
SUPERSET_IMAGE_TAG="${SUPERSET_IMAGE_TAG}" \
SUPERSET_IMAGE_PULL_POLICY="${SUPERSET_IMAGE_PULL_POLICY}" \
bash "${SCRIPT_DIR}/infra-up.sh"

echo "==> Phase 2/2: deploying dynamic local artifacts..."
NAMESPACE="${NAMESPACE}" \
PROJECT_CODE_IMAGE_REPOSITORY="${PROJECT_CODE_IMAGE_REPOSITORY}" \
PROJECT_CODE_IMAGE_TAG="${PROJECT_CODE_IMAGE_TAG}" \
bash "${SCRIPT_DIR}/deploy-artifacts.sh"

echo ""
echo "OpenLakeForge local stack is up."
echo ""
echo "Run 'make local-forward' to port-forward all services, then open:"
echo ""
echo "  Dagster UI:       http://localhost:3000"
echo "  Superset UI:      http://localhost:8088  (admin / admin)"
echo "  OpenMetadata UI:  http://localhost:8585  (admin@open-metadata.org / admin)"
echo "  Trino UI:         http://localhost:8080"
echo "  Polaris API:      http://localhost:8181/api/catalog"
echo "  SeaweedFS S3:     http://localhost:9000"
