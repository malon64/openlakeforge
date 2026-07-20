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

configure_deployment_scope() {
  local docker_endpoint=""
  local docker_config_source="${DOCKER_CONFIG:-${HOME}/.docker}"
  local docker_config_target="${OPENLAKEFORGE_DOCKER_CONFIG:-${REPO_ROOT}/.tmp/docker/${DEPLOYMENT_SCOPE}}"

  : "${REPO_ROOT:?REPO_ROOT must be set before configuring deployment scope}"
  : "${DEPLOYMENT_SCOPE:?DEPLOYMENT_SCOPE must be local, azure, or aws}"
  : "${KUBECONFIG_PATH:?KUBECONFIG_PATH must identify the provider kubeconfig}"
  : "${KUBE_CONTEXT:?KUBE_CONTEXT must identify the target cluster}"

  # Docker stores its selected context alongside credentials under
  # DOCKER_CONFIG. Resolve the selected engine first so provider-scoped
  # credential directories do not accidentally fall back to /var/run/docker.sock
  # (notably when the host uses Colima or another non-default context).
  if [[ -z "${DOCKER_HOST:-}" ]] && command -v docker &>/dev/null; then
    docker_endpoint="$(docker context inspect "$(docker context show)" --format '{{.Endpoints.docker.Host}}' 2>/dev/null || true)"
    if [[ -n "${docker_endpoint}" ]]; then
      export DOCKER_HOST="${docker_endpoint}"
    fi
  fi

  export KUBECONFIG="${KUBECONFIG_PATH}"
  export KUBE_CONTEXT
  export HELM_CACHE_SCOPE="${HELM_CACHE_SCOPE:-${DEPLOYMENT_SCOPE}}"
  export DOCKER_BUILDKIT="${DOCKER_BUILDKIT:-1}"
  export BUILDKIT_PROGRESS="${BUILDKIT_PROGRESS:-plain}"
  export DOCKER_CONFIG="${docker_config_target}"
  export SUPERSET_REPORT_WORK_DIR="${SUPERSET_REPORT_WORK_DIR:-${REPO_ROOT}/.tmp/superset-reports/${DEPLOYMENT_SCOPE}}"
  export OPENLAKEFORGE_PORT_FORWARD_LOG_PREFIX="${OPENLAKEFORGE_PORT_FORWARD_LOG_PREFIX:-/tmp/openlakeforge-${DEPLOYMENT_SCOPE}}"

  mkdir -p \
    "$(dirname "${KUBECONFIG_PATH}")" \
    "${DOCKER_CONFIG}" \
    "${SUPERSET_REPORT_WORK_DIR}"

  # CLI plugins are executable tooling, not credentials. Keep them available
  # from the provider-scoped config so `docker build` continues to use BuildKit.
  if [[ -d "${docker_config_source}/cli-plugins" && ! -e "${DOCKER_CONFIG}/cli-plugins" ]]; then
    ln -s "${docker_config_source}/cli-plugins" "${DOCKER_CONFIG}/cli-plugins"
  fi
}

require_kube_context() {
  local contexts
  contexts="$(kubectl config get-contexts -o name 2>/dev/null || true)"
  if ! grep -Fxq "${KUBE_CONTEXT}" <<<"${contexts}"; then
    echo "ERROR: Kubernetes context '${KUBE_CONTEXT}' is absent from isolated kubeconfig '${KUBECONFIG}'." >&2
    exit 1
  fi
  kubectl --context "${KUBE_CONTEXT}" cluster-info >/dev/null
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
