#!/usr/bin/env bash
# Shared shell helpers for OpenLakeForge scripts. Intended to be sourced.
#
# Callers may tune retry behavior with RUN_RETRY_ATTEMPTS and
# RUN_RETRY_DELAY_SECONDS before calling run_with_retry.

require_cmd() {
  local cmd="$1"
  if ! command -v "${cmd}" &>/dev/null; then
    echo "ERROR: '${cmd}' not found on PATH" >&2
    exit 1
  fi
}

check_prereqs() {
  local missing=0
  local cmd
  for cmd in "$@"; do
    if ! command -v "${cmd}" &>/dev/null; then
      echo "ERROR: '${cmd}' not found on PATH" >&2
      missing=1
    fi
  done
  [[ "${missing}" -eq 0 ]] || exit 1
}

git_or_time_tag() {
  git -C "${REPO_ROOT:-.}" rev-parse --short HEAD 2>/dev/null || date -u +%Y%m%d%H%M%S
}

run_with_retry() {
  local description="$1"
  shift

  local max_attempts="${RUN_RETRY_ATTEMPTS:-4}"
  local delay_seconds="${RUN_RETRY_DELAY_SECONDS:-20}"
  local attempt=1

  while true; do
    if "$@"; then
      return 0
    else
      local status=$?
    fi

    if ((attempt >= max_attempts)); then
      echo "ERROR: ${description} failed after ${attempt} attempt(s)." >&2
      return "${status}"
    fi

    echo "WARN: ${description} failed on attempt ${attempt}/${max_attempts}; retrying in ${delay_seconds}s..." >&2
    sleep "${delay_seconds}"
    attempt=$((attempt + 1))
  done
}
