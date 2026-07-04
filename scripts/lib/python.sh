#!/usr/bin/env bash
# Shared entrypoint for the OpenLakeForge Python tooling package. Intended to
# be sourced after common.sh (for require_cmd).
#
# The tooling lives in tools/olf and is managed with uv; `uv run` resolves and
# syncs the package environment on demand, so callers only need uv installed.

OLF_PROJECT_DIR="${OLF_PROJECT_DIR:-${REPO_ROOT}/tools/olf}"

olf_run() {
  require_cmd uv
  uv run --project "${OLF_PROJECT_DIR}" --quiet olf "$@"
}
