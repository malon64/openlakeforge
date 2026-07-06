#!/usr/bin/env bash
# Load runtime defaults from Terraform provider contracts when available.
#
# This file is intended to be sourced. The resolution logic lives in the
# openlakeforge-tools package (`olf contracts env` in tools/olf); this wrapper
# evaluates its export/unset lines so shell callers keep the same environment
# surface across local, Azure, and AWS. Local fallback values still apply
# before the stack is applied, so tests and profile parsing keep working.

OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR="${OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR:-infra/terraform/environments/local}"

_olf_contracts_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_olf_repo_root="$(cd "${_olf_contracts_dir}/../.." && pwd)"
OLF_PROJECT_DIR="${OLF_PROJECT_DIR:-${_olf_repo_root}/tools/olf}"

# The contract resolver runs as a subprocess, so a shell-local (non-exported)
# NAMESPACE would be invisible to it. Export it when the caller set one.
if [[ -n "${NAMESPACE:-}" ]]; then
  export NAMESPACE
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: 'uv' is required to resolve OpenLakeForge runtime contracts." >&2
  echo "Install it from https://docs.astral.sh/uv/ (for example: brew install uv)." >&2
  return 1 2>/dev/null || exit 1
fi

if ! _olf_contract_env_exports="$(uv run --project "${OLF_PROJECT_DIR}" --quiet olf contracts env \
  --terraform-dir "${OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR}")"; then
  echo "ERROR: failed to resolve OpenLakeForge runtime contracts." >&2
  return 1 2>/dev/null || exit 1
fi

if ! eval "${_olf_contract_env_exports}"; then
  echo "ERROR: failed to load OpenLakeForge runtime contract exports." >&2
  return 1 2>/dev/null || exit 1
fi
unset _olf_contract_env_exports
