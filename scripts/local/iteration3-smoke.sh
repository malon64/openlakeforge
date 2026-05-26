#!/usr/bin/env bash
# Launch the Iteration 3 Sales Bronze-to-Silver job and verify Silver tables through Trino.
set -euo pipefail

NAMESPACE="${NAMESPACE:-lakehouse}"
export DAGSTER_SMOKE_JOB="${DAGSTER_SMOKE_JOB:-iteration3_sales_silver_job}"

bash scripts/local/dagster-smoke.sh

echo "==> Verifying Silver Iceberg tables through Trino"
trino_pod="$(kubectl get pods -n "${NAMESPACE}" -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' | grep '^trino-coordinator' | head -n 1 || true)"
if [[ -z "${trino_pod}" ]]; then
  echo "ERROR: Trino coordinator pod not found in namespace '${NAMESPACE}'." >&2
  exit 1
fi

kubectl wait --for=condition=Ready "pod/${trino_pod}" -n "${NAMESPACE}" --timeout=180s

for table in sales customers products; do
  echo "==> Querying iceberg.sales.${table}"
  kubectl exec -n "${NAMESPACE}" "${trino_pod}" -- \
    trino --execute "SELECT count(*) FROM iceberg.sales.${table}"
done

echo "Iteration 3 Sales Silver smoke test passed."
