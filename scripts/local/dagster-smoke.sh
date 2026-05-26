#!/usr/bin/env bash
# Launch the Iteration 2 Dagster smoke job and verify the Kubernetes run pod.
set -euo pipefail

NAMESPACE="${NAMESPACE:-lakehouse}"
JOB_NAME="${DAGSTER_SMOKE_JOB:-iteration2_smoke_job}"
LOCAL_PORT="${DAGSTER_LOCAL_PORT:-3000}"

for cmd in kubectl python3; do
  if ! command -v "${cmd}" &>/dev/null; then
    echo "ERROR: '${cmd}' not found on PATH" >&2
    exit 1
  fi
done

echo "==> Waiting for Dagster webserver pod..."
webserver_pod=""
for _ in $(seq 1 60); do
  webserver_pod="$(kubectl get pods -n "${NAMESPACE}" -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null | grep 'dagster-webserver' | head -n 1 || true)"
  if [[ -n "${webserver_pod}" ]]; then
    break
  fi
  sleep 5
done

if [[ -z "${webserver_pod}" ]]; then
  echo "ERROR: Dagster webserver pod not found in namespace '${NAMESPACE}'." >&2
  exit 1
fi

kubectl wait --for=condition=Ready "pod/${webserver_pod}" -n "${NAMESPACE}" --timeout=180s

echo "==> Port-forwarding Dagster webserver on http://localhost:${LOCAL_PORT}"
kubectl port-forward "pod/${webserver_pod}" "${LOCAL_PORT}:80" -n "${NAMESPACE}" >/tmp/openlakeforge-dagster-port-forward.log 2>&1 &
port_forward_pid=$!
trap 'kill "${port_forward_pid}" 2>/dev/null || true' EXIT

for _ in $(seq 1 30); do
  if python3 - <<PY >/dev/null 2>&1
import urllib.request
urllib.request.urlopen("http://127.0.0.1:${LOCAL_PORT}/server_info", timeout=2).read()
PY
  then
    break
  fi
  sleep 2
done

echo "==> Launching Dagster job '${JOB_NAME}'"
run_id="$(DAGSTER_URL="http://127.0.0.1:${LOCAL_PORT}/graphql" DAGSTER_JOB_NAME="${JOB_NAME}" python3 - <<'PY'
import json
import os
import sys
import time
import urllib.error
import urllib.request

url = os.environ["DAGSTER_URL"]
job_name = os.environ["DAGSTER_JOB_NAME"]


def graphql(query, variables=None):
    payload = json.dumps({"query": query, "variables": variables or {}}).encode()
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        data = json.loads(response.read().decode())
    if data.get("errors"):
        raise RuntimeError(data["errors"])
    return data["data"]


repositories_query = """
query Repositories {
  repositoriesOrError {
    __typename
    ... on RepositoryConnection {
      nodes {
        name
        location { name }
        pipelines { name }
      }
    }
    ... on PythonError {
      message
      stack
    }
  }
}
"""

launch_mutation = """
mutation LaunchRun($repositoryLocationName: String!, $repositoryName: String!, $jobName: String!) {
  launchRun(executionParams: {
    selector: {
      repositoryLocationName: $repositoryLocationName
      repositoryName: $repositoryName
      jobName: $jobName
    }
    runConfigData: {}
  }) {
    __typename
    ... on LaunchRunSuccess {
      run { runId status }
    }
    ... on RunConfigValidationInvalid {
      errors { message }
    }
    ... on PythonError {
      message
      stack
    }
  }
}
"""

run_query = """
query RunStatus($runId: ID!) {
  runOrError(runId: $runId) {
    __typename
    ... on Run {
      runId
      status
    }
    ... on PythonError {
      message
      stack
    }
  }
}
"""

repo_nodes = None
for _ in range(60):
    result = graphql(repositories_query)["repositoriesOrError"]
    if result["__typename"] == "RepositoryConnection":
        repo_nodes = result["nodes"]
        break
    time.sleep(5)

if repo_nodes is None:
    raise SystemExit("Dagster repositories did not load")

selector = None
for repo in repo_nodes:
    for pipeline in repo.get("pipelines", []):
        if pipeline["name"] == job_name:
            selector = {
                "repositoryLocationName": repo["location"]["name"],
                "repositoryName": repo["name"],
                "jobName": job_name,
            }
            break
    if selector:
        break

if selector is None:
    available = {
        repo["location"]["name"]: [pipeline["name"] for pipeline in repo.get("pipelines", [])]
        for repo in repo_nodes
    }
    raise SystemExit(f"Job {job_name!r} not found. Available jobs: {available}")

launch = graphql(launch_mutation, selector)["launchRun"]
if launch["__typename"] != "LaunchRunSuccess":
    raise SystemExit(f"Dagster launch failed: {launch}")

run_id = launch["run"]["runId"]

for _ in range(120):
    run = graphql(run_query, {"runId": run_id})["runOrError"]
    if run["__typename"] != "Run":
        raise SystemExit(f"Could not query run {run_id}: {run}")
    status = run["status"]
    if status == "SUCCESS":
        print(run_id)
        sys.exit(0)
    if status in {"FAILURE", "CANCELED"}:
        raise SystemExit(f"Run {run_id} ended with status {status}")
    time.sleep(5)

raise SystemExit(f"Run {run_id} did not complete before timeout")
PY
)"

echo "Dagster run succeeded: ${run_id}"

echo "==> Verifying Kubernetes run pod/job for run id"
if kubectl get pods,jobs -n "${NAMESPACE}" -o json | python3 -c "import json,sys; data=json.load(sys.stdin); rid='${run_id}'; matches=[item['kind']+'/'+item['metadata']['name'] for item in data.get('items', []) if rid in json.dumps(item.get('metadata', {}))]; print('\n'.join(matches)); sys.exit(0 if matches else 1)"; then
  echo "Kubernetes run pod/job found for ${run_id}"
else
  echo "ERROR: No Kubernetes pod/job metadata matched Dagster run id ${run_id}." >&2
  exit 1
fi
