#!/usr/bin/env bash
# Shared Kubernetes helpers. Intended to be sourced after common.sh.
#
# All helpers operate in the namespace named by NAMESPACE (default: lakehouse).

: "${NAMESPACE:=lakehouse}"

secret_value() {
  local secret_name="$1"
  local key="$2"

  kubectl --context "${KUBE_CONTEXT}" get secret "${secret_name}" -n "${NAMESPACE}" \
    -o "jsonpath={.data.${key}}" | base64 -d
}

# Delete every job whose name starts with the given prefix.
cleanup_jobs_by_prefix() {
  local prefix="$1"
  local job

  while IFS= read -r job; do
    [[ -n "${job}" ]] || continue
    kubectl --context "${KUBE_CONTEXT}" delete "${job}" -n "${NAMESPACE}" --ignore-not-found
  done < <(
    kubectl --context "${KUBE_CONTEXT}" get jobs -n "${NAMESPACE}" -o name 2>/dev/null \
      | grep "^job.batch/${prefix}" || true
  )
}

# Delete only jobs with the given name prefix that report failed pods.
cleanup_failed_jobs_by_prefix() {
  local prefix="$1"
  local failed
  local job

  while IFS= read -r job; do
    [[ -n "${job}" ]] || continue
    failed="$(kubectl --context "${KUBE_CONTEXT}" get "${job}" -n "${NAMESPACE}" -o jsonpath='{.status.failed}' 2>/dev/null || true)"
    if [[ -n "${failed}" && "${failed}" != "0" ]]; then
      kubectl --context "${KUBE_CONTEXT}" delete "${job}" -n "${NAMESPACE}" --ignore-not-found
    fi
  done < <(
    kubectl --context "${KUBE_CONTEXT}" get jobs -n "${NAMESPACE}" -o name 2>/dev/null \
      | grep "^job.batch/${prefix}" || true
  )
}

# If the namespace already exists in the cluster but the Terraform state lost
# its kubernetes_namespace resource, import it before apply to avoid a hard
# "already exists" failure on re-bootstrap.
import_namespace_if_missing_in_state() {
  local terraform_dir="$1"
  local resource_addr="$2"
  local namespace="$3"
  shift 3

  if terraform -chdir="${terraform_dir}" state show "${resource_addr}" >/dev/null 2>&1; then
    return 0
  fi

  if ! kubectl --context "${KUBE_CONTEXT}" get namespace "${namespace}" >/dev/null 2>&1; then
    return 0
  fi

  echo "==> Importing existing namespace '${namespace}' into Terraform state..."
  terraform -chdir="${terraform_dir}" import "$@" "${resource_addr}" "${namespace}" >/dev/null
}

# Preflight the Polaris service-principal credentials. When Polaris restarted
# with in-memory persistence, previously minted client credentials are stale;
# force a new bootstrap generation so Terraform re-runs the bootstrap job.
# Sets POLARIS_BOOTSTRAP_GENERATION in the caller's environment. The OAuth token
# check itself lives in `olf polaris check-credentials`; this reacts to its exit
# code (3 == stale). Callers must have sourced scripts/lib/python.sh (olf_run).
prepare_polaris_bootstrap_generation() {
  local status

  if olf_run polaris check-credentials; then
    return 0
  else
    status=$?
  fi

  if [[ "${status}" -eq 3 ]]; then
    POLARIS_BOOTSTRAP_GENERATION="rebootstrap-$(date -u +%Y%m%d%H%M%S)"
    cleanup_jobs_by_prefix "polaris-bootstrap-"
    cleanup_jobs_by_prefix "openmetadata-bootstrap-"
    cleanup_failed_jobs_by_prefix "openmetadata-polaris-refresh-"
    return 0
  fi

  return "${status}"
}
