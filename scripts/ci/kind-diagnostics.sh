#!/usr/bin/env bash
# Collect bounded kind smoke diagnostics before GitHub Actions removes the runner.
set -euo pipefail

OUTPUT_DIR="${1:?diagnostic output directory is required}"
NAMESPACE="${NAMESPACE:-lakehouse}"
KUBE_CONTEXT="${KUBE_CONTEXT:?KUBE_CONTEXT is required}"

mkdir -p "${OUTPUT_DIR}"
free -h >"${OUTPUT_DIR}/host-memory.txt" 2>&1 || true
df -h >"${OUTPUT_DIR}/host-disk.txt" 2>&1 || true
docker system df >"${OUTPUT_DIR}/docker-disk.txt" 2>&1 || true
kubectl --context "${KUBE_CONTEXT}" get nodes -o wide >"${OUTPUT_DIR}/nodes.txt" 2>&1 || true
kubectl --context "${KUBE_CONTEXT}" get pods --all-namespaces -o wide >"${OUTPUT_DIR}/all-pods.txt" 2>&1 || true
kubectl --context "${KUBE_CONTEXT}" get events --all-namespaces --sort-by=.lastTimestamp >"${OUTPUT_DIR}/all-events.txt" 2>&1 || true
kubectl --context "${KUBE_CONTEXT}" get all -n "${NAMESPACE}" -o wide >"${OUTPUT_DIR}/workloads.txt" 2>&1 || true
kubectl --context "${KUBE_CONTEXT}" get events -n "${NAMESPACE}" --sort-by=.lastTimestamp >"${OUTPUT_DIR}/events.txt" 2>&1 || true
kubectl --context "${KUBE_CONTEXT}" describe pods -n "${NAMESPACE}" >"${OUTPUT_DIR}/pod-descriptions.txt" 2>&1 || true

while IFS= read -r pod; do
  kubectl --context "${KUBE_CONTEXT}" logs -n "${NAMESPACE}" "${pod}" --all-containers --tail=200 \
    >"${OUTPUT_DIR}/${pod}.log" 2>&1 || true
done < <(kubectl --context "${KUBE_CONTEXT}" get pods -n "${NAMESPACE}" -o name 2>/dev/null | sed 's#pod/##')
