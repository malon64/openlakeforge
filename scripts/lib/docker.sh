#!/usr/bin/env bash
# Shared Docker helpers for retrying registry-sensitive operations.

docker_pull_with_retries() {
  local attempts="${DOCKER_PULL_ATTEMPTS:-${DOCKER_REGISTRY_ATTEMPTS:-3}}"
  local delay_seconds="${DOCKER_PULL_RETRY_DELAY_SECONDS:-${DOCKER_REGISTRY_RETRY_DELAY_SECONDS:-10}}"
  local attempt=1

  while true; do
    if docker pull "$@"; then
      return 0
    fi

    if ((attempt >= attempts)); then
      echo "ERROR: docker pull failed after ${attempts} attempts: docker pull $*" >&2
      return 1
    fi

    echo "WARN: docker pull failed; retrying in ${delay_seconds}s (${attempt}/${attempts})..." >&2
    sleep "${delay_seconds}"
    attempt=$((attempt + 1))
  done
}

docker_build_with_retries() {
  local attempts="${DOCKER_BUILD_ATTEMPTS:-${DOCKER_REGISTRY_ATTEMPTS:-3}}"
  local delay_seconds="${DOCKER_BUILD_RETRY_DELAY_SECONDS:-${DOCKER_REGISTRY_RETRY_DELAY_SECONDS:-10}}"
  local attempt=1

  while true; do
    if docker build "$@"; then
      return 0
    fi

    if ((attempt >= attempts)); then
      echo "ERROR: docker build failed after ${attempts} attempts." >&2
      return 1
    fi

    echo "WARN: docker build failed; retrying in ${delay_seconds}s (${attempt}/${attempts})..." >&2
    sleep "${delay_seconds}"
    attempt=$((attempt + 1))
  done
}

docker_push_with_retries() {
  local attempts="${DOCKER_PUSH_ATTEMPTS:-${DOCKER_REGISTRY_ATTEMPTS:-3}}"
  local delay_seconds="${DOCKER_PUSH_RETRY_DELAY_SECONDS:-${DOCKER_REGISTRY_RETRY_DELAY_SECONDS:-10}}"
  local attempt=1

  while true; do
    if docker push "$@"; then
      return 0
    fi

    if ((attempt >= attempts)); then
      echo "ERROR: docker push failed after ${attempts} attempts: docker push $*" >&2
      return 1
    fi

    echo "WARN: docker push failed; retrying in ${delay_seconds}s (${attempt}/${attempts})..." >&2
    sleep "${delay_seconds}"
    attempt=$((attempt + 1))
  done
}
