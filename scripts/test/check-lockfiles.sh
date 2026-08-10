#!/usr/bin/env bash
# Verify every Python lockfile is in sync with its pyproject.toml, using uv's
# own resolver rather than a hand-rolled PEP 508 parser: an added, removed, or
# range-tightened dependency that was never relocked would otherwise still
# install from `images/project-code/pyproject.toml` metadata in
# check-project-code.sh while the Dockerfile installs the stale, hash-pinned
# lock with --no-deps.
set -euo pipefail

require_cmd() {
  local cmd="$1"
  if ! command -v "${cmd}" &>/dev/null; then
    printf "ERROR: '%s' not found on PATH\n" "${cmd}" >&2
    exit 1
  fi
}

require_cmd uv

echo "==> Checking tools/olf/uv.lock is in sync with tools/olf/pyproject.toml"
uv lock --project tools/olf --check

PYPROJECT="images/project-code/pyproject.toml"
LOCK="images/project-code/requirements.lock"

[[ -f "${PYPROJECT}" ]] || { echo "ERROR: ${PYPROJECT} not found" >&2; exit 1; }
[[ -s "${LOCK}" ]] || { echo "ERROR: ${LOCK} missing or empty -- generate it with the command in its own header" >&2; exit 1; }

echo "==> Checking ${LOCK} is in sync with ${PYPROJECT}"

# Re-run the exact command recorded in the lockfile's own header (line 2),
# redirected to a temp file seeded with the committed lock so uv resolves
# against today's already-locked versions rather than latest-on-the-internet.
compile_command="$(sed -n '2p' "${LOCK}" | sed 's/^#[[:space:]]*//')"
if [[ "${compile_command}" != "uv pip compile "* ]]; then
  echo "ERROR: ${LOCK} header does not start with the expected 'uv pip compile' command" >&2
  exit 1
fi

tmp_lock="$(mktemp)"
trap 'rm -f "${tmp_lock}"' EXIT
cp "${LOCK}" "${tmp_lock}"
compile_command="${compile_command/--output-file ${LOCK}/--output-file ${tmp_lock}}"

if ! ${compile_command} --quiet >/tmp/check-lockfiles-compile.log 2>&1; then
  echo "ERROR: ${PYPROJECT} declares a constraint that ${LOCK}'s locked versions no longer satisfy:" >&2
  cat /tmp/check-lockfiles-compile.log >&2
  echo "Regenerate with: ${compile_command/--output-file ${tmp_lock}/--output-file ${LOCK}}" >&2
  exit 1
fi

if ! diff -q <(tail -n +3 "${LOCK}") <(tail -n +3 "${tmp_lock}") >/dev/null; then
  echo "ERROR: ${LOCK} does not match a fresh compile of ${PYPROJECT} (a dependency was added, removed, or" >&2
  echo "        its resolved transitive closure changed). Regenerate with:" >&2
  echo "        ${compile_command/--output-file ${tmp_lock}/--output-file ${LOCK}}" >&2
  diff <(tail -n +3 "${LOCK}") <(tail -n +3 "${tmp_lock}") >&2 || true
  exit 1
fi

echo "Lockfiles are in sync."
