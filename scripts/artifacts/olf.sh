#!/usr/bin/env bash
# Load the provider contract environment, then run an olf subcommand.
#
# Standalone artifact Make targets (floe-manifest-upload, superset-reports-*,
# openmetadata-metadata-deploy) use this so they get the same contract
# environment the full deploy-artifacts flow sources.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
export OPENLAKEFORGE_REPO_ROOT="${REPO_ROOT}"

# shellcheck source=scripts/lib/common.sh
source "${REPO_ROOT}/scripts/lib/common.sh"
# shellcheck source=scripts/lib/python.sh
source "${REPO_ROOT}/scripts/lib/python.sh"
# shellcheck source=/dev/null
source "${REPO_ROOT}/scripts/contracts/load-runtime-env.sh"

cd "${REPO_ROOT}"
olf_run "$@"
