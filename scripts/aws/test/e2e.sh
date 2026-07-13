#!/usr/bin/env bash
# Thin wrapper for AWS POC e2e smoke validation.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
export OPENLAKEFORGE_REPO_ROOT="${REPO_ROOT}"
export OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR="${OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR:-${REPO_ROOT}/infra/terraform/environments/aws-poc}"

# shellcheck source=scripts/lib/common.sh
source "${REPO_ROOT}/scripts/lib/common.sh"
# shellcheck source=scripts/lib/python.sh
source "${REPO_ROOT}/scripts/lib/python.sh"

cd "${REPO_ROOT}"
olf_run e2e run --env aws
