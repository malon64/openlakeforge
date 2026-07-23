#!/usr/bin/env bash
# Shared Helm chart cache helpers. Intended to be sourced after common.sh.
#
# Requires REPO_ROOT to be set by the caller. Chart repository/version/package
# variables keep their existing per-script names and defaults.

HELM_CACHE_SCOPE="${HELM_CACHE_SCOPE:-shared}"
HELM_REPOSITORY_CONFIG="${HELM_REPOSITORY_CONFIG:-${REPO_ROOT}/.tmp/helm/${HELM_CACHE_SCOPE}/repositories.yaml}"
HELM_REPOSITORY_CACHE="${HELM_REPOSITORY_CACHE:-${REPO_ROOT}/.tmp/helm/${HELM_CACHE_SCOPE}/repository-cache}"
HELM_CHART_CACHE_DIR="${HELM_CHART_CACHE_DIR:-${REPO_ROOT}/.tmp/helm/${HELM_CACHE_SCOPE}/charts}"

helm_cached() {
  HELM_REPOSITORY_CONFIG="${HELM_REPOSITORY_CONFIG}" \
    HELM_REPOSITORY_CACHE="${HELM_REPOSITORY_CACHE}" \
    helm "$@"
}

prepare_helm_cache_dirs() {
  mkdir -p "$(dirname "${HELM_REPOSITORY_CONFIG}")" "${HELM_REPOSITORY_CACHE}" "${HELM_CHART_CACHE_DIR}"
}

# prepare_cached_chart <display-name> <repo-name> <repo-url> <chart-ref> <version> <package-path>
# Downloads a chart package into the shared cache unless a valid cached copy exists.
prepare_cached_chart() {
  local display_name="$1"
  local repo_name="$2"
  local repo_url="$3"
  local chart_ref="$4"
  local version="$5"
  local package_path="$6"

  if [[ -f "${package_path}" ]] && helm show chart "${package_path}" >/dev/null 2>&1; then
    echo "==> Using cached ${display_name} Helm chart: ${package_path}"
    return 0
  fi

  rm -f "${package_path}"

  echo "==> Downloading ${display_name} Helm chart ${version} into local cache..."
  run_with_retry "Helm repo add ${display_name}" \
    helm_cached repo add "${repo_name}" "${repo_url}" --force-update
  run_with_retry "Helm repo update" \
    helm_cached repo update
  run_with_retry "${display_name} Helm chart download" \
    helm_cached pull "${chart_ref}" --version "${version}" --destination "${HELM_CHART_CACHE_DIR}"
}

# prepare_cached_dagster_chart_no_schema <repo-name> <repo-url> <version> <package-path>
# The Dagster chart ships a values.schema.json that rejects the OpenLakeForge
# values overlay, so the cached package is re-packed without it.
prepare_cached_dagster_chart_no_schema() {
  local repo_name="$1"
  local repo_url="$2"
  local version="$3"
  local package_path="$4"

  if [[ -f "${package_path}" ]] && helm show chart "${package_path}" >/dev/null 2>&1; then
    echo "==> Using cached Dagster Helm chart: ${package_path}"
    return 0
  fi

  local dagster_work_dir
  dagster_work_dir="$(mktemp -d "${REPO_ROOT}/.tmp/dagster-chart.XXXXXX")"
  rm -f "${package_path}"

  echo "==> Downloading Dagster Helm chart ${version} into local cache..."
  run_with_retry "Helm repo add Dagster" \
    helm_cached repo add "${repo_name}" "${repo_url}" --force-update
  run_with_retry "Helm repo update" \
    helm_cached repo update
  run_with_retry "Dagster Helm chart download" \
    helm_cached pull "${repo_name}/dagster" --version "${version}" --untar --untardir "${dagster_work_dir}"

  find "${dagster_work_dir}/dagster" -name "values.schema.json" -delete
  helm package "${dagster_work_dir}/dagster" --destination "${HELM_CHART_CACHE_DIR}" >/dev/null
  mv "${HELM_CHART_CACHE_DIR}/dagster-${version}.tgz" "${package_path}"
  rm -rf "${dagster_work_dir}"
}
