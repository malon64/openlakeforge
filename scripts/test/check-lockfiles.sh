#!/usr/bin/env bash
# Verify every Python lockfile is in sync with its pyproject.toml, using uv's
# own resolver rather than a hand-rolled PEP 508 parser: an added, removed, or
# range-tightened dependency that was never relocked would otherwise still
# install from `images/project-code/pyproject.toml` metadata in
# check-project-code.sh while the Dockerfile installs the stale, hash-pinned
# lock with --no-deps.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

require_cmd() {
  local cmd="$1"
  if ! command -v "${cmd}" &>/dev/null; then
    printf "ERROR: '%s' not found on PATH\n" "${cmd}" >&2
    exit 1
  fi
}

require_cmd uv

catalog_python_locks="$(uv run --project tools/olf --locked python -c '
from pathlib import PurePosixPath

import yaml

with open("release/component-catalog.yaml") as catalog_file:
    catalog = yaml.safe_load(catalog_file)

locks = ((catalog.get("components") or {}).get("python") or {})
if not isinstance(locks, dict) or not locks:
    raise SystemExit("ERROR: components.python must be a non-empty mapping")

for name, path in sorted(locks.items()):
    if not isinstance(path, str) or not path:
        raise SystemExit(f"ERROR: components.python.{name} must be a non-empty path")
    pure_path = PurePosixPath(path)
    if pure_path.is_absolute() or ".." in pure_path.parts:
        raise SystemExit(f"ERROR: components.python.{name} must be a repository-relative path")
    print(f"{name}\t{path}")
')"

check_uv_lock() {
  local lock_path="$1"
  local project_dir
  project_dir="$(dirname "${lock_path}")"

  [[ -f "${project_dir}/pyproject.toml" ]] || {
    echo "ERROR: ${project_dir}/pyproject.toml not found for ${lock_path}" >&2
    exit 1
  }
  [[ -s "${lock_path}" ]] || { echo "ERROR: ${lock_path} missing or empty" >&2; exit 1; }

  echo "==> Checking ${lock_path} is in sync with ${project_dir}/pyproject.toml"
  uv lock --project "${project_dir}" --check
}

check_compiled_lock() {
  local lock_path="$1"
  local project_dir
  local pyproject
  local compile_command
  local tmp_lock

  project_dir="$(dirname "${lock_path}")"
  pyproject="${project_dir}/pyproject.toml"
  [[ -f "${pyproject}" ]] || { echo "ERROR: ${pyproject} not found" >&2; exit 1; }
  [[ -s "${lock_path}" ]] || {
    echo "ERROR: ${lock_path} missing or empty -- generate it with the command in its own header" >&2
    exit 1
  }

  echo "==> Checking ${lock_path} is in sync with ${pyproject}"

  # Re-run the exact command recorded in the lockfile's own header (line 2),
  # redirected to a temp file seeded with the committed lock so uv resolves
  # against today's already-locked versions rather than latest-on-the-internet.
  compile_command="$(sed -n '2p' "${lock_path}" | sed 's/^#[[:space:]]*//')"
  if [[ "${compile_command}" != "uv pip compile "* ]]; then
    echo "ERROR: ${lock_path} header does not start with the expected 'uv pip compile' command" >&2
    exit 1
  fi

  tmp_lock="$(mktemp)"
  cp "${lock_path}" "${tmp_lock}"
  compile_command="${compile_command/--output-file ${lock_path}/--output-file ${tmp_lock}}"

  if ! ${compile_command} --quiet >/tmp/check-lockfiles-compile.log 2>&1; then
    echo "ERROR: ${pyproject} declares a constraint that ${lock_path}'s locked versions no longer satisfy:" >&2
    cat /tmp/check-lockfiles-compile.log >&2
    echo "Regenerate with: ${compile_command/--output-file ${tmp_lock}/--output-file ${lock_path}}" >&2
    rm -f "${tmp_lock}"
    exit 1
  fi

  if ! diff -q <(tail -n +3 "${lock_path}") <(tail -n +3 "${tmp_lock}") >/dev/null; then
    echo "ERROR: ${lock_path} does not match a fresh compile of ${pyproject} (a dependency was added, removed, or" >&2
    echo "        its resolved transitive closure changed). Regenerate with:" >&2
    echo "        ${compile_command/--output-file ${tmp_lock}/--output-file ${lock_path}}" >&2
    diff <(tail -n +3 "${lock_path}") <(tail -n +3 "${tmp_lock}") >&2 || true
    rm -f "${tmp_lock}"
    exit 1
  fi

  rm -f "${tmp_lock}"
}

while IFS=$'\t' read -r lock_name lock_path; do
  case "$(basename "${lock_path}")" in
    uv.lock)
      check_uv_lock "${lock_path}"
      ;;
    *)
      check_compiled_lock "${lock_path}"
      ;;
  esac
done <<< "${catalog_python_locks}"

echo "Lockfiles are in sync."
