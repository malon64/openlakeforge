#!/usr/bin/env bash
# Run Azure POC end-to-end validation against the AKS stack.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
FOUNDATION_TERRAFORM_DIR="${REPO_ROOT}/infra/terraform/foundations/azure-aks"
NAMESPACE="${NAMESPACE:-lakehouse}"
AZURE_CLUSTER_NAME="${AZURE_CLUSTER_NAME:-aks-openlakeforge-poc}"
KUBE_CONTEXT="${KUBE_CONTEXT:-}"
DAGSTER_LOCAL_PORT="${DAGSTER_LOCAL_PORT:-13000}"
SUPERSET_LOCAL_PORT="${SUPERSET_LOCAL_PORT:-18088}"
OPENMETADATA_LOCAL_PORT="${OPENMETADATA_LOCAL_PORT:-18585}"

require_cmd() {
  local cmd="$1"
  if ! command -v "${cmd}" &>/dev/null; then
    echo "ERROR: '${cmd}' not found on PATH" >&2
    exit 1
  fi
}

prepare_aks_context() {
  AZURE_RESOURCE_GROUP="$(terraform -chdir="${FOUNDATION_TERRAFORM_DIR}" output -raw resource_group_name)"
  AZURE_CLUSTER_NAME="$(terraform -chdir="${FOUNDATION_TERRAFORM_DIR}" output -raw cluster_name)"
  KUBE_CONTEXT="${KUBE_CONTEXT:-${AZURE_CLUSTER_NAME}}"

  az aks get-credentials \
    --resource-group "${AZURE_RESOURCE_GROUP}" \
    --name "${AZURE_CLUSTER_NAME}" \
    --overwrite-existing >/dev/null

  if ! kubectl cluster-info --context "${KUBE_CONTEXT}" >/dev/null 2>&1; then
    echo "ERROR: Kubernetes context '${KUBE_CONTEXT}' is not reachable." >&2
    exit 1
  fi

  kubectl config use-context "${KUBE_CONTEXT}" >/dev/null
}

port_forward_pids=()
cleanup() {
  for pid in "${port_forward_pids[@]:-}"; do
    kill "${pid}" >/dev/null 2>&1 || true
  done
}
trap cleanup EXIT

start_port_forward() {
  local resource="$1"
  local mapping="$2"
  local name="$3"

  echo "==> Port-forwarding ${name} on ${mapping}..."
  kubectl --context "${KUBE_CONTEXT}" port-forward "${resource}" "${mapping}" -n "${NAMESPACE}" \
    >"/tmp/openlakeforge-azure-${name}-port-forward.log" 2>&1 &
  port_forward_pids+=("$!")
  sleep 2
}

wait_http() {
  local url="$1"
  python3 - "${url}" <<'PY'
import sys
import time
import urllib.error
import urllib.request

url = sys.argv[1]
last_error = None
for _ in range(90):
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            if response.status < 500:
                raise SystemExit(0)
    except Exception as exc:  # noqa: BLE001
        last_error = exc
    time.sleep(2)
raise SystemExit(f"ERROR: endpoint did not become reachable: {url}: {last_error}")
PY
}

check_pods_ready() {
  echo "==> Checking pod health..."
  local pods_json
  pods_json="$(mktemp)"
  kubectl --context "${KUBE_CONTEXT}" get pods -n "${NAMESPACE}" -o json >"${pods_json}"
  python3 - "${pods_json}" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text())
bad = []
for item in payload.get("items", []):
    name = item["metadata"]["name"]
    phase = item.get("status", {}).get("phase")
    if phase == "Succeeded":
        continue
    if phase == "Running":
        statuses = item.get("status", {}).get("containerStatuses", [])
        unready = [status["name"] for status in statuses if not status.get("ready")]
        if not unready:
            continue
        bad.append(f"{name}: Running but containers not ready: {', '.join(unready)}")
        continue
    bad.append(f"{name}: {phase}")

if bad:
    raise SystemExit("ERROR: unhealthy pods:\n" + "\n".join(bad))
PY
  rm -f "${pods_json}"
}

trino_scalar() {
  local sql="$1"
  kubectl --context "${KUBE_CONTEXT}" exec -n "${NAMESPACE}" deploy/trino-coordinator -- \
    trino --output-format CSV_UNQUOTED --execute "${sql}" \
    | awk 'NF { last = $0 } END { gsub(/\r/, "", last); print last }'
}

check_trino() {
  echo "==> Checking Trino catalogs..."
  kubectl --context "${KUBE_CONTEXT}" exec -n "${NAMESPACE}" deploy/trino-coordinator -- \
    trino --output-format CSV_UNQUOTED --execute "SHOW CATALOGS" | grep -qx "iceberg"

  echo "==> Checking Silver and Gold table counts..."
  silver_count="$(trino_scalar "SELECT count(*) FROM iceberg.information_schema.tables WHERE table_schema IN ('sales_order_revenue_silver', 'sales_customer_health_silver', 'supply_chain_inventory_reliability_silver')")"
  gold_count="$(trino_scalar "SELECT count(*) FROM iceberg.information_schema.tables WHERE table_schema IN ('sales_order_revenue_gold', 'sales_customer_health_gold', 'supply_chain_inventory_reliability_gold')")"

  if [[ "${silver_count}" != "15" ]]; then
    echo "ERROR: expected 15 Silver tables, got ${silver_count}" >&2
    exit 1
  fi

  if [[ "${gold_count}" != "9" ]]; then
    echo "ERROR: expected 9 Gold marts, got ${gold_count}" >&2
    exit 1
  fi

  local marts=(
    sales_order_revenue_gold.mart_order_revenue_by_day
    sales_order_revenue_gold.mart_order_revenue_by_channel
    sales_order_revenue_gold.mart_order_revenue_margin_by_product
    sales_customer_health_gold.mart_customer_health_score
    sales_customer_health_gold.mart_churn_risk_by_segment
    sales_customer_health_gold.mart_support_sla_by_customer
    supply_chain_inventory_reliability_gold.mart_inventory_position
    supply_chain_inventory_reliability_gold.mart_supplier_delivery_reliability
    supply_chain_inventory_reliability_gold.mart_stockout_risk
  )

  for mart in "${marts[@]}"; do
    count="$(trino_scalar "SELECT count(*) FROM iceberg.${mart}")"
    if [[ "${count}" -le 0 ]]; then
      echo "ERROR: expected iceberg.${mart} to contain rows, got ${count}" >&2
      exit 1
    fi
  done
}

launch_and_poll_dagster_jobs() {
  echo "==> Launching and polling Dagster product jobs..."
  start_port_forward "svc/dagster-dagster-webserver" "${DAGSTER_LOCAL_PORT}:80" "dagster"
  wait_http "http://127.0.0.1:${DAGSTER_LOCAL_PORT}/server_info"

  python3 - "http://127.0.0.1:${DAGSTER_LOCAL_PORT}/graphql" <<'PY'
import json
import sys
import time
import urllib.error
import urllib.request

GRAPHQL_URL = sys.argv[1]
JOBS = [
    "sales_order_revenue_pipeline",
    "sales_customer_health_pipeline",
    "supply_chain_inventory_reliability_pipeline",
]


def graphql(query, variables=None):
    payload = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
    req = urllib.request.Request(
        GRAPHQL_URL,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GraphQL HTTP {err.code}: {body}") from err

    data = json.loads(body)
    if data.get("errors"):
        raise RuntimeError(json.dumps(data["errors"], indent=2))
    return data["data"]


def discover_repository(job_name):
    query = """
    query Workspace {
      workspaceOrError {
        __typename
        ... on Workspace {
          locationEntries {
            name
            locationOrLoadError {
              __typename
              ... on RepositoryLocation {
                repositories {
                  name
                  pipelines {
                    name
                  }
                }
              }
              ... on PythonError {
                message
              }
            }
          }
        }
      }
    }
    """
    try:
        workspace = graphql(query)["workspaceOrError"]
    except Exception:
        return "openlakeforge-dagster", "__repository__"

    for entry in workspace.get("locationEntries", []):
        location = entry.get("locationOrLoadError") or {}
        if location.get("__typename") != "RepositoryLocation":
            continue
        for repo in location.get("repositories", []):
            pipeline_names = {pipeline["name"] for pipeline in repo.get("pipelines", [])}
            if job_name in pipeline_names:
                return entry["name"], repo["name"]
    return "openlakeforge-dagster", "__repository__"


def launch(job_name):
    location_name, repository_name = discover_repository(job_name)
    mutation = """
    mutation LaunchRun($executionParams: ExecutionParams!) {
      launchRun(executionParams: $executionParams) {
        __typename
        ... on LaunchRunSuccess {
          run {
            runId
            status
          }
        }
        ... on RunConfigValidationInvalid {
          errors {
            message
          }
        }
        ... on PythonError {
          message
          stack
        }
      }
    }
    """
    variables = {
        "executionParams": {
            "selector": {
                "repositoryLocationName": location_name,
                "repositoryName": repository_name,
                "pipelineName": job_name,
            },
            "runConfigData": {},
            "mode": "default",
        }
    }
    result = graphql(mutation, variables)["launchRun"]
    if result["__typename"] != "LaunchRunSuccess":
        raise RuntimeError(f"Failed to launch {job_name}: {json.dumps(result, indent=2)}")
    return result["run"]["runId"]


def poll(job_name, run_id):
    query = """
    query Run($runId: ID!) {
      runOrError(runId: $runId) {
        __typename
        ... on Run {
          status
        }
        ... on PythonError {
          message
        }
      }
    }
    """
    terminal = {"SUCCESS", "FAILURE", "CANCELED"}
    for _ in range(180):
        result = graphql(query, {"runId": run_id})["runOrError"]
        if result["__typename"] != "Run":
            raise RuntimeError(f"Could not read run {run_id}: {result}")
        status = result["status"]
        if status in terminal:
            if status != "SUCCESS":
                raise RuntimeError(f"{job_name} run {run_id} ended with {status}")
            print(f"{job_name}: SUCCESS ({run_id})")
            return
        time.sleep(10)
    raise RuntimeError(f"{job_name} run {run_id} did not finish within 30 minutes")


for job in JOBS:
    run_id = launch(job)
    poll(job, run_id)
PY
}

check_superset_reports() {
  echo "==> Checking Superset report imports..."
  start_port_forward "svc/superset" "${SUPERSET_LOCAL_PORT}:8088" "superset"
  wait_http "http://127.0.0.1:${SUPERSET_LOCAL_PORT}/health"

  python3 - "http://127.0.0.1:${SUPERSET_LOCAL_PORT}" <<'PY'
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = sys.argv[1].rstrip("/")
EXPECTED = {
    "sales-order-revenue": "Sales Order Revenue",
    "sales-customer-health": "Sales Customer Health",
    "supply-chain-inventory-reliability": "Supply Chain Inventory Reliability",
}


def request(method, path, token=None, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{BASE_URL}{path}", data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


last_error = None
for _ in range(60):
    try:
        token = request(
            "POST",
            "/api/v1/security/login",
            payload={"username": "admin", "password": "admin", "provider": "db", "refresh": True},
        )["access_token"]
        break
    except Exception as exc:  # noqa: BLE001
        last_error = exc
        time.sleep(2)
else:
    raise SystemExit(f"ERROR: Superset login failed: {last_error}")

query = urllib.parse.quote(json.dumps({"page_size": 100}))
dashboards = request("GET", f"/api/v1/dashboard/?q={query}", token=token).get("result", [])
by_slug = {dashboard.get("slug"): dashboard.get("dashboard_title") for dashboard in dashboards}
titles = {dashboard.get("dashboard_title") for dashboard in dashboards}
missing = [
    f"{slug} ({title})"
    for slug, title in EXPECTED.items()
    if by_slug.get(slug) != title and title not in titles
]
if missing:
    raise SystemExit("ERROR: missing Superset dashboards: " + ", ".join(missing))
PY
}

check_openmetadata_assets() {
  echo "==> Checking OpenMetadata domains and data products..."
  start_port_forward "svc/openmetadata" "${OPENMETADATA_LOCAL_PORT}:8585" "openmetadata"
  wait_http "http://127.0.0.1:${OPENMETADATA_LOCAL_PORT}/api/v1/system/config/jwks"

  python3 - "http://127.0.0.1:${OPENMETADATA_LOCAL_PORT}" <<'PY'
import base64
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = sys.argv[1].rstrip("/")
DOMAINS = ["sales", "supply_chain"]
DATA_PRODUCTS = {
    "sales_order_revenue": ["sales_order_revenue", "sales.sales_order_revenue"],
    "sales_customer_health": ["sales_customer_health", "sales.sales_customer_health"],
    "supply_chain_inventory_reliability": [
        "supply_chain_inventory_reliability",
        "supply_chain.supply_chain_inventory_reliability",
    ],
}


def request(method, path, token=None, payload=None, ok_statuses=(200,)):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{BASE_URL}{path}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            body = response.read().decode("utf-8")
            status = response.status
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", errors="replace")
        status = err.code
    if status not in ok_statuses:
        raise RuntimeError(f"{method} {path} failed with HTTP {status}: {body}")
    return json.loads(body) if body else {}


encoded_password = base64.b64encode(b"admin").decode("ascii")
last_error = None
for _ in range(60):
    try:
        token = request(
            "POST",
            "/api/v1/users/login",
            payload={"email": "admin@open-metadata.org", "password": encoded_password},
        )["accessToken"]
        break
    except Exception as exc:  # noqa: BLE001
        last_error = exc
        time.sleep(2)
else:
    raise SystemExit(f"ERROR: OpenMetadata login failed: {last_error}")

for domain in DOMAINS:
    request("GET", f"/api/v1/domains/name/{urllib.parse.quote(domain)}", token=token)

for product, candidates in DATA_PRODUCTS.items():
    last_error = None
    for candidate in candidates:
        try:
            request("GET", f"/api/v1/dataProducts/name/{urllib.parse.quote(candidate)}", token=token)
            break
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    else:
        raise SystemExit(f"ERROR: missing OpenMetadata data product {product}: {last_error}")
PY
}

for cmd in az grep kubectl python3 terraform; do
  require_cmd "${cmd}"
done

prepare_aks_context
check_pods_ready
launch_and_poll_dagster_jobs
check_trino
check_superset_reports
check_openmetadata_assets

echo "Azure POC end-to-end validation passed."
