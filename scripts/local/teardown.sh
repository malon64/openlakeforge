#!/usr/bin/env bash
# Tear down the OpenLakeForge local stack.
# Uninstalls all Helm releases and deletes the namespace.
# The kind cluster itself is left intact.
set -euo pipefail

NAMESPACE="${NAMESPACE:-lakehouse}"

echo "==> Uninstalling Helm releases from namespace '${NAMESPACE}'..."
for release in trino polaris seaweedfs garage; do
  if helm status "${release}" -n "${NAMESPACE}" &>/dev/null; then
    echo "    Uninstalling ${release}..."
    helm uninstall "${release}" -n "${NAMESPACE}"
  else
    echo "    ${release} not installed, skipping."
  fi
done

echo "==> Deleting namespace '${NAMESPACE}'..."
kubectl delete namespace "${NAMESPACE}" --ignore-not-found

echo "Teardown complete. Kind cluster is still running."
echo "To delete the cluster: kind delete cluster --name openlakeforge-local"
