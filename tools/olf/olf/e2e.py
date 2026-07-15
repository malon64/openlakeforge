"""End-to-end validation for OpenLakeForge environments.

The public Make targets stay as thin wrappers; this module owns the runtime
checks that used to live in Azure/AWS bash scripts.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import boto3
import requests
from botocore.config import Config

from olf import contracts, k8s, log

Environment = Literal["local", "azure", "aws"]
Suite = Literal["full", "smoke"]

PRODUCT_JOBS = (
    "sales_order_revenue_pipeline",
    "sales_customer_health_pipeline",
    "supply_chain_inventory_reliability_pipeline",
)

GOLD_MARTS = (
    "sales_order_revenue_gold.mart_order_revenue_by_day",
    "sales_order_revenue_gold.mart_order_revenue_by_channel",
    "sales_order_revenue_gold.mart_order_revenue_margin_by_product",
    "sales_customer_health_gold.mart_customer_health_score",
    "sales_customer_health_gold.mart_churn_risk_by_segment",
    "sales_customer_health_gold.mart_support_sla_by_customer",
    "supply_chain_inventory_reliability_gold.mart_inventory_position",
    "supply_chain_inventory_reliability_gold.mart_supplier_delivery_reliability",
    "supply_chain_inventory_reliability_gold.mart_stockout_risk",
)

EXPECTED_DASHBOARDS = {
    "sales-order-revenue": "Sales Order Revenue",
    "sales-customer-health": "Sales Customer Health",
    "supply-chain-inventory-reliability": "Supply Chain Inventory Reliability",
}

EXPECTED_DOMAINS = ("sales", "supply_chain")
EXPECTED_DATA_PRODUCTS = {
    "sales_order_revenue": ("sales_order_revenue", "sales.sales_order_revenue"),
    "sales_customer_health": ("sales_customer_health", "sales.sales_customer_health"),
    "supply_chain_inventory_reliability": (
        "supply_chain_inventory_reliability",
        "supply_chain.supply_chain_inventory_reliability",
    ),
}

EXPECTED_GLUE_SCHEMAS = {
    "sales_order_revenue_silver",
    "sales_order_revenue_gold",
    "sales_customer_health_silver",
    "sales_customer_health_gold",
    "supply_chain_inventory_reliability_silver",
    "supply_chain_inventory_reliability_gold",
}

MANIFEST_KEYS = (
    "floe/manifests/sales/order_revenue/order_revenue.manifest.json",
    "floe/manifests/sales/customer_health/customer_health.manifest.json",
    "floe/manifests/supply_chain/inventory_reliability/inventory_reliability.manifest.json",
)

ARTIFACT_PREFIXES = (
    "floe/reports/",
    "run-artifacts/dbt/",
    "logs/dagster/compute/",
)

DAGSTER_JOB_TIMEOUT_SECONDS = 1800


class E2EError(RuntimeError):
    pass


class DagsterTransientError(E2EError):
    pass


@dataclass(frozen=True)
class E2EConfig:
    env: Environment
    suite: Suite
    namespace: str
    kube_context: str
    repo_root: Path
    foundation_terraform_dir: Path | None
    contract_terraform_dir: Path
    aws_region: str | None = None
    dagster_local_port: int | None = None
    superset_local_port: int | None = None
    openmetadata_local_port: int | None = None
    seaweedfs_local_port: int | None = None


def run(
    env: Environment,
    *,
    suite: Suite | None = None,
    namespace: str = "lakehouse",
    kube_context: str = "",
    repo_root: Path | None = None,
) -> None:
    cfg = prepare_config(env, suite=suite, namespace=namespace, kube_context=kube_context, repo_root=repo_root)
    check_commands(cfg)
    prepare_kube_context(cfg)
    check_pods_ready(cfg)
    if cfg.suite == "smoke":
        run_smoke(cfg)
    else:
        run_full(cfg)
    log.info(f"{cfg.env.capitalize()} OpenLakeForge {cfg.suite} e2e validation passed.")


def default_suite(env: Environment) -> Suite:
    return "smoke" if env == "aws" else "full"


def prepare_config(
    env: Environment,
    *,
    suite: Suite | None,
    namespace: str,
    kube_context: str,
    repo_root: Path | None,
) -> E2EConfig:
    root = (repo_root or Path(os.environ.get("OPENLAKEFORGE_REPO_ROOT", "."))).resolve()
    actual_suite = suite or default_suite(env)
    if env == "local":
        cluster_name = os.environ.get("CLUSTER_NAME", "openlakeforge-local")
        return E2EConfig(
            env=env,
            suite=actual_suite,
            namespace=namespace,
            kube_context=kube_context or os.environ.get("KUBE_CONTEXT", f"kind-{cluster_name}"),
            repo_root=root,
            foundation_terraform_dir=root / "infra/terraform/foundations/local-kind",
            contract_terraform_dir=_contract_dir(root, "infra/terraform/environments/local"),
            dagster_local_port=int(os.environ.get("DAGSTER_LOCAL_PORT", "13000")),
            superset_local_port=int(os.environ.get("SUPERSET_LOCAL_PORT", "18088")),
            openmetadata_local_port=int(os.environ.get("OPENMETADATA_LOCAL_PORT", "18585")),
            seaweedfs_local_port=int(os.environ.get("SEAWEEDFS_LOCAL_PORT", "19000")),
        )
    if env == "azure":
        cluster_name = os.environ.get("AZURE_CLUSTER_NAME", "aks-openlakeforge-poc")
        return E2EConfig(
            env=env,
            suite=actual_suite,
            namespace=namespace,
            kube_context=kube_context or os.environ.get("KUBE_CONTEXT", cluster_name),
            repo_root=root,
            foundation_terraform_dir=root / "infra/terraform/foundations/azure-aks",
            contract_terraform_dir=_contract_dir(root, "infra/terraform/environments/azure-poc"),
            dagster_local_port=int(os.environ.get("DAGSTER_LOCAL_PORT", "13000")),
            superset_local_port=int(os.environ.get("SUPERSET_LOCAL_PORT", "18088")),
            openmetadata_local_port=int(os.environ.get("OPENMETADATA_LOCAL_PORT", "18585")),
            seaweedfs_local_port=int(os.environ.get("SEAWEEDFS_LOCAL_PORT", "19000")),
        )
    if env == "aws":
        cluster_name = os.environ.get("AWS_CLUSTER_NAME", "limited-eks-openlakeforge-poc")
        return E2EConfig(
            env=env,
            suite=actual_suite,
            namespace=namespace,
            kube_context=kube_context or os.environ.get("KUBE_CONTEXT", cluster_name),
            repo_root=root,
            foundation_terraform_dir=root / "infra/terraform/foundations/aws-eks",
            contract_terraform_dir=_contract_dir(root, "infra/terraform/environments/aws-poc"),
            aws_region=os.environ.get("AWS_REGION"),
            dagster_local_port=int(os.environ.get("DAGSTER_LOCAL_PORT", "13000")),
            superset_local_port=int(os.environ.get("SUPERSET_LOCAL_PORT", "18088")),
            openmetadata_local_port=int(os.environ.get("OPENMETADATA_LOCAL_PORT", "18585")),
        )
    raise E2EError(f"unsupported e2e environment: {env}")


def _contract_dir(repo_root: Path, default: str) -> Path:
    return Path(os.environ.get("OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR", repo_root / default)).resolve()


def check_commands(cfg: E2EConfig) -> None:
    commands = ["kubectl", "terraform"]
    if cfg.env == "azure":
        commands.append("az")
    if cfg.env == "aws":
        commands.append("aws")
    missing = [cmd for cmd in commands if shutil.which(cmd) is None]
    if missing:
        raise E2EError(f"missing required command(s): {', '.join(missing)}")


def prepare_kube_context(cfg: E2EConfig) -> None:
    if cfg.env == "local" and kube_context_is_ready(cfg.kube_context):
        _run(["kubectl", "config", "use-context", cfg.kube_context], capture=True)
        return

    if cfg.env == "azure":
        if cfg.foundation_terraform_dir is None:
            raise E2EError("Azure e2e requires a foundation Terraform directory.")
        resource_group = terraform_output(cfg.foundation_terraform_dir, "resource_group_name")
        cluster_name = terraform_output(cfg.foundation_terraform_dir, "cluster_name")
        _run(
            [
                "az",
                "aks",
                "get-credentials",
                "--resource-group",
                resource_group,
                "--name",
                cluster_name,
                "--overwrite-existing",
            ]
        )
    elif cfg.env == "aws":
        if cfg.foundation_terraform_dir is None:
            raise E2EError("AWS e2e requires a foundation Terraform directory.")
        region = terraform_output(cfg.foundation_terraform_dir, "aws_region")
        cluster_name = terraform_output(cfg.foundation_terraform_dir, "cluster_name")
        _run(
            [
                "aws",
                "eks",
                "update-kubeconfig",
                "--region",
                region,
                "--name",
                cluster_name,
                "--alias",
                cfg.kube_context,
            ]
        )
    _run_retry(["kubectl", "cluster-info", "--context", cfg.kube_context], capture=True, attempts=6, delay=5)
    _run(["kubectl", "config", "use-context", cfg.kube_context], capture=True)


def kube_context_is_ready(kube_context: str) -> bool:
    try:
        _run(["kubectl", "cluster-info", "--context", kube_context], capture=True)
    except E2EError:
        return False
    return True


def run_smoke(cfg: E2EConfig) -> None:
    if cfg.env == "aws":
        check_aws_provider_contracts(cfg)
        check_aws_storage_and_glue(cfg)
    check_trino_catalog(cfg)


def run_full(cfg: E2EConfig) -> None:
    launch_and_poll_dagster_jobs(cfg)
    check_trino_tables_and_marts(cfg)
    check_superset_dashboards(cfg)
    check_openmetadata_assets(cfg)
    check_ops_artifacts(cfg)


def check_pods_ready(cfg: E2EConfig) -> None:
    log.step("Checking pod health...")
    bad: list[str] = []
    last_error: E2EError | None = None
    for _ in range(60):
        try:
            payload = json.loads(kubectl(cfg, ["get", "pods", "-n", cfg.namespace, "-o", "json"], capture=True))
        except E2EError as exc:
            last_error = exc
            time.sleep(5)
            continue
        bad = unhealthy_pod_messages(payload)
        if not bad:
            return
        time.sleep(5)
    if last_error is not None and not bad:
        raise last_error
    raise E2EError("unhealthy pods:\n" + "\n".join(bad))


def unhealthy_pod_messages(payload: Mapping[str, Any]) -> list[str]:
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
    return bad


def check_trino_catalog(cfg: E2EConfig) -> None:
    log.step("Checking Trino catalogs...")
    catalogs = kubectl(
        cfg,
        [
            "exec",
            "-n",
            cfg.namespace,
            "deploy/trino-coordinator",
            "--",
            "trino",
            "--output-format",
            "CSV_UNQUOTED",
            "--execute",
            "SHOW CATALOGS",
        ],
        capture=True,
    )
    if "iceberg" not in set(catalogs.splitlines()):
        raise E2EError("Trino did not expose the iceberg catalog.")


def check_trino_tables_and_marts(cfg: E2EConfig) -> None:
    check_trino_catalog(cfg)
    log.step("Checking Silver and Gold table counts...")
    silver_count = trino_scalar(
        cfg,
        "SELECT count(*) FROM iceberg.information_schema.tables "
        "WHERE table_schema IN ('sales_order_revenue_silver', 'sales_customer_health_silver', "
        "'supply_chain_inventory_reliability_silver')",
    )
    gold_count = trino_scalar(
        cfg,
        "SELECT count(*) FROM iceberg.information_schema.tables "
        "WHERE table_schema IN ('sales_order_revenue_gold', 'sales_customer_health_gold', "
        "'supply_chain_inventory_reliability_gold')",
    )
    assert_scalar_equals(silver_count, "15", "Silver table count")
    assert_scalar_equals(gold_count, "9", "Gold mart count")

    for mart in GOLD_MARTS:
        count = trino_scalar(cfg, f"SELECT count(*) FROM iceberg.{mart}")
        if int(count) <= 0:
            raise E2EError(f"expected iceberg.{mart} to contain rows, got {count}")


def trino_scalar(cfg: E2EConfig, sql: str) -> str:
    output = kubectl(
        cfg,
        [
            "exec",
            "-n",
            cfg.namespace,
            "deploy/trino-coordinator",
            "--",
            "trino",
            "--output-format",
            "CSV_UNQUOTED",
            "--execute",
            sql,
        ],
        capture=True,
    )
    return parse_trino_scalar(output)


def parse_trino_scalar(output: str) -> str:
    values = [line.strip().replace("\r", "") for line in output.splitlines() if line.strip()]
    if not values:
        raise E2EError("Trino query returned no scalar value.")
    return values[-1]


def assert_scalar_equals(actual: str, expected: str, label: str) -> None:
    if actual != expected:
        raise E2EError(f"expected {label} {expected}, got {actual}")


def launch_and_poll_dagster_jobs(cfg: E2EConfig) -> None:
    log.step("Launching and polling Dagster product jobs...")
    assert cfg.dagster_local_port is not None
    log_path = f"/tmp/openlakeforge-{cfg.env}-dagster-port-forward.log"
    with k8s.port_forward(
        "dagster-dagster-webserver",
        80,
        cfg.namespace,
        local_port=cfg.dagster_local_port,
        log_path=log_path,
    ):
        base_url = f"http://127.0.0.1:{cfg.dagster_local_port}"
        if not k8s.http_wait(f"{base_url}/server_info", attempts=90, delay=2):
            raise E2EError("Dagster endpoint did not become reachable.")
        client = DagsterClient(f"{base_url}/graphql")
        timeout_seconds = int(os.environ.get("DAGSTER_JOB_TIMEOUT_SECONDS", str(DAGSTER_JOB_TIMEOUT_SECONDS)))
        for job in PRODUCT_JOBS:
            run_id = client.launch(job)
            log.info(f"{job}: launched ({run_id})")
            client.poll(job, run_id, timeout_seconds=timeout_seconds)


class DagsterClient:
    def __init__(
        self,
        graphql_url: str,
        *,
        request_json: Callable[[str, Mapping[str, Any] | None], Mapping[str, Any]] | None = None,
    ) -> None:
        self.graphql_url = graphql_url
        self._request_json = request_json or self._requests_graphql

    def launch(self, job_name: str) -> str:
        location_name, repository_name = self.wait_for_repository(job_name)
        result = self.graphql(
            """
            mutation LaunchRun($executionParams: ExecutionParams!) {
              launchRun(executionParams: $executionParams) {
                __typename
                ... on LaunchRunSuccess { run { runId status } }
                ... on RunConfigValidationInvalid { errors { message } }
                ... on PythonError { message stack }
              }
            }
            """,
            {
                "executionParams": {
                    "selector": {
                        "repositoryLocationName": location_name,
                        "repositoryName": repository_name,
                        "pipelineName": job_name,
                    },
                    "runConfigData": {},
                    "mode": "default",
                }
            },
        )["launchRun"]
        if result["__typename"] != "LaunchRunSuccess":
            raise E2EError(f"failed to launch {job_name}: {json.dumps(result, indent=2)}")
        return result["run"]["runId"]

    def wait_for_repository(
        self,
        job_name: str,
        *,
        timeout_seconds: int = 90,
        delay: float = 2.0,
    ) -> tuple[str, str]:
        deadline = time.monotonic() + timeout_seconds
        last_error: E2EError | None = None
        while time.monotonic() < deadline:
            try:
                return self.discover_repository(job_name)
            except E2EError as exc:
                last_error = exc
                self.try_reload_repository_location(expected_repository_location_name(job_name))
                time.sleep(delay)
        detail = f": {last_error}" if last_error else ""
        raise E2EError(
            f"Dagster repository for {job_name} did not become ready "
            f"within {timeout_seconds} seconds{detail}"
        )

    def try_reload_repository_location(self, location_name: str) -> None:
        try:
            self.graphql(
                """
                mutation ReloadRepositoryLocation($repositoryLocationName: String!) {
                  reloadRepositoryLocation(repositoryLocationName: $repositoryLocationName) {
                    __typename
                    ... on WorkspaceLocationEntry { name }
                    ... on ReloadNotSupported { message }
                    ... on RepositoryLocationNotFound { message }
                    ... on PythonError { message }
                  }
                }
                """,
                {"repositoryLocationName": location_name},
            )
        except E2EError:
            return

    def poll(
        self,
        job_name: str,
        run_id: str,
        *,
        timeout_seconds: int = DAGSTER_JOB_TIMEOUT_SECONDS,
        delay: float = 10.0,
        attempts: int | None = None,
    ) -> None:
        terminal = {"SUCCESS", "FAILURE", "CANCELED"}
        last_error: Exception | None = None
        last_status: str | None = None
        remaining_attempts = attempts
        deadline = time.monotonic() + timeout_seconds
        while remaining_attempts is None or remaining_attempts > 0:
            if remaining_attempts is None and time.monotonic() >= deadline:
                break
            if remaining_attempts is not None:
                remaining_attempts -= 1
            try:
                result = self.graphql(
                    """
                    query Run($runId: ID!) {
                      runOrError(runId: $runId) {
                        __typename
                        ... on Run { status }
                        ... on PythonError { message }
                      }
                    }
                    """,
                    {"runId": run_id},
                )["runOrError"]
            except DagsterTransientError as exc:
                last_error = exc
                time.sleep(delay)
                continue
            if result["__typename"] != "Run":
                raise E2EError(f"could not read run {run_id}: {result}")
            status = result["status"]
            if status != last_status:
                log.info(f"{job_name}: {status} ({run_id})")
                last_status = status
            if status in terminal:
                if status != "SUCCESS":
                    raise E2EError(f"{job_name} run {run_id} ended with {status}")
                return
            time.sleep(delay)
        detail = f": {last_error}" if last_error else ""
        raise E2EError(f"{job_name} run {run_id} did not finish within {timeout_seconds} seconds{detail}")

    def discover_repository(self, job_name: str) -> tuple[str, str]:
        workspace = self.graphql(
            """
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
                          pipelines { name }
                          jobs { name }
                        }
                      }
                      ... on PythonError { message }
                    }
                  }
                }
              }
            }
            """,
        )["workspaceOrError"]
        if workspace.get("__typename") != "Workspace":
            raise E2EError(f"Dagster workspace query failed: {workspace}")

        load_errors: list[str] = []
        for entry in workspace.get("locationEntries", []):
            location = entry.get("locationOrLoadError") or {}
            if location.get("__typename") != "RepositoryLocation":
                if location.get("__typename") == "PythonError":
                    message = location.get("message", "unknown Dagster workspace error").strip()
                    load_errors.append(f"{entry.get('name')}: {message}")
                continue
            for repo in location.get("repositories", []):
                job_names = {job["name"] for job in repo.get("jobs", [])}
                pipeline_names = {pipeline["name"] for pipeline in repo.get("pipelines", [])}
                if job_name in job_names or job_name in pipeline_names:
                    return entry["name"], repo["name"]
        detail = f" Workspace load errors: {'; '.join(load_errors)}" if load_errors else ""
        raise E2EError(f"Dagster job {job_name} is not available yet.{detail}")

    def graphql(self, query: str, variables: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        data = self._request_json(query, variables)
        if data.get("errors"):
            raise E2EError(json.dumps(data["errors"], indent=2))
        return data["data"]

    def _requests_graphql(self, query: str, variables: Mapping[str, Any] | None) -> Mapping[str, Any]:
        try:
            response = requests.post(
                self.graphql_url,
                json={"query": query, "variables": variables or {}},
                headers={"Accept": "application/json"},
                timeout=30,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise DagsterTransientError(f"Dagster GraphQL request failed: {exc}") from exc
        return response.json()
def check_superset_dashboards(cfg: E2EConfig) -> None:
    log.step("Checking Superset report imports...")
    assert cfg.superset_local_port is not None
    log_path = f"/tmp/openlakeforge-{cfg.env}-superset-port-forward.log"
    with k8s.port_forward("superset", 8088, cfg.namespace, local_port=cfg.superset_local_port, log_path=log_path):
        base_url = f"http://127.0.0.1:{cfg.superset_local_port}"
        if not k8s.http_wait(f"{base_url}/health", attempts=90, delay=2):
            raise E2EError("Superset endpoint did not become reachable.")
        dashboards = SupersetClient(base_url).dashboards()
    assert_superset_dashboards(dashboards, EXPECTED_DASHBOARDS)


def expected_repository_location_name(job_name: str) -> str:
    if job_name.startswith("supply_chain_"):
        return "supply-chain-dagster"
    return "sales-dagster"


class SupersetClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def dashboards(self) -> list[Mapping[str, Any]]:
        token = self._login()
        response = requests.get(
            f"{self.base_url}/api/v1/dashboard/",
            params={"q": json.dumps({"page_size": 100})},
            headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
            timeout=30,
        )
        response.raise_for_status()
        return response.json().get("result", [])

    def _login(self) -> str:
        last_error: Exception | None = None
        for _ in range(60):
            try:
                response = requests.post(
                    f"{self.base_url}/api/v1/security/login",
                    json={"username": "admin", "password": "admin", "provider": "db", "refresh": True},
                    headers={"Accept": "application/json"},
                    timeout=30,
                )
                response.raise_for_status()
                return str(response.json()["access_token"])
            except Exception as exc:
                last_error = exc
                time.sleep(2)
        raise E2EError(f"Superset login failed: {last_error}")


def assert_superset_dashboards(
    dashboards: list[Mapping[str, Any]], expected: Mapping[str, str] = EXPECTED_DASHBOARDS
) -> None:
    by_slug = {dashboard.get("slug"): dashboard.get("dashboard_title") for dashboard in dashboards}
    titles = {dashboard.get("dashboard_title") for dashboard in dashboards}
    missing = [
        f"{slug} ({title})"
        for slug, title in expected.items()
        if by_slug.get(slug) != title and title not in titles
    ]
    if missing:
        raise E2EError("missing Superset dashboards: " + ", ".join(missing))


def check_openmetadata_assets(cfg: E2EConfig) -> None:
    log.step("Checking OpenMetadata domains and data products...")
    assert cfg.openmetadata_local_port is not None
    log_path = f"/tmp/openlakeforge-{cfg.env}-openmetadata-port-forward.log"
    with k8s.port_forward(
        "openmetadata",
        8585,
        cfg.namespace,
        local_port=cfg.openmetadata_local_port,
        log_path=log_path,
    ):
        base_url = f"http://127.0.0.1:{cfg.openmetadata_local_port}"
        if not k8s.http_wait(f"{base_url}/api/v1/system/config/jwks", attempts=90, delay=2):
            raise E2EError("OpenMetadata endpoint did not become reachable.")
        OpenMetadataE2EClient(base_url).assert_assets()


class OpenMetadataE2EClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def assert_assets(self) -> None:
        token = self._login()
        for domain in EXPECTED_DOMAINS:
            self._request("GET", f"/api/v1/domains/name/{domain}", token)
        for product, candidates in EXPECTED_DATA_PRODUCTS.items():
            if not self._first_existing_data_product(candidates, token):
                raise E2EError(f"missing OpenMetadata data product {product}")

    def _first_existing_data_product(self, candidates: tuple[str, ...], token: str) -> str | None:
        for candidate in candidates:
            if self._request("GET", f"/api/v1/dataProducts/name/{candidate}", token, ok_statuses=(200, 404))[0] == 200:
                return candidate
        return None

    def _login(self) -> str:
        encoded_password = base64.b64encode(b"admin").decode("ascii")
        last_error: Exception | None = None
        for _ in range(60):
            try:
                status, payload = self._request(
                    "POST",
                    "/api/v1/users/login",
                    "",
                    payload={"email": "admin@open-metadata.org", "password": encoded_password},
                )
                if status == 200:
                    return str(payload["accessToken"])
            except Exception as exc:
                last_error = exc
                time.sleep(2)
        raise E2EError(f"OpenMetadata login failed: {last_error}")

    def _request(
        self,
        method: str,
        path: str,
        token: str,
        *,
        payload: Mapping[str, Any] | None = None,
        ok_statuses: tuple[int, ...] = (200,),
    ) -> tuple[int, Mapping[str, Any]]:
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        response = requests.request(method, f"{self.base_url}{path}", json=payload, headers=headers, timeout=30)
        if response.status_code not in ok_statuses:
            raise E2EError(f"{method} {path} failed with HTTP {response.status_code}: {response.text}")
        return response.status_code, response.json() if response.text else {}


def check_ops_artifacts(cfg: E2EConfig) -> None:
    log.step("Checking ops artifact bucket contents...")
    trigger_log_archive_job(cfg)
    provider_contracts = load_provider_contracts_or_raise(cfg)
    bucket = provider_contracts["artifact_bucket"]["bucket_name"]
    if cfg.env == "aws":
        client = boto3.client("s3", region_name=aws_stack_region(cfg))
        assert_ops_artifacts(client, bucket, cfg.namespace)
        return

    assert cfg.seaweedfs_local_port is not None
    access_key_id = k8s.secret_value("seaweedfs-s3-creds", "AWS_ACCESS_KEY_ID", cfg.namespace)
    secret_access_key = k8s.secret_value("seaweedfs-s3-creds", "AWS_SECRET_ACCESS_KEY", cfg.namespace)
    log_path = f"/tmp/openlakeforge-{cfg.env}-seaweedfs-s3-port-forward.log"
    with k8s.port_forward("seaweedfs-s3", 8333, cfg.namespace, local_port=cfg.seaweedfs_local_port, log_path=log_path):
        endpoint = f"http://127.0.0.1:{cfg.seaweedfs_local_port}"
        client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="us-east-1",
            config=Config(s3={"addressing_style": "path"}),
        )
        wait_for_bucket(client, bucket, endpoint)
        assert_ops_artifacts(client, bucket, cfg.namespace)


def trigger_log_archive_job(cfg: E2EConfig) -> None:
    archive_job = f"openlakeforge-k8s-log-archive-e2e-{int(time.time())}"
    kubectl(
        cfg,
        [
            "create",
            "job",
            "--from=cronjob/openlakeforge-k8s-log-archive",
            archive_job,
            "-n",
            cfg.namespace,
        ],
    )
    kubectl(
        cfg,
        [
            "wait",
            "--for=condition=complete",
            f"job/{archive_job}",
            "-n",
            cfg.namespace,
            "--timeout=300s",
        ],
    )


def assert_ops_artifacts(client: Any, bucket: str, namespace: str) -> None:
    for key in MANIFEST_KEYS:
        client.head_object(Bucket=bucket, Key=key)
    for prefix in (*ARTIFACT_PREFIXES, f"logs/k8s/namespace={namespace}/"):
        require_s3_prefix(client, bucket, prefix)


def wait_for_bucket(client: Any, bucket: str, endpoint: str, *, attempts: int = 60, delay: float = 2.0) -> None:
    for attempt in range(1, attempts + 1):
        try:
            client.head_bucket(Bucket=bucket)
            return
        except Exception as exc:
            if attempt == attempts:
                raise E2EError(f"bucket '{bucket}' did not become available through {endpoint}.") from exc
            time.sleep(delay)


def require_s3_prefix(client: Any, bucket: str, prefix: str) -> None:
    result = client.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=1)
    if not result.get("Contents"):
        raise E2EError(f"expected objects under s3://{bucket}/{prefix}")


def check_aws_provider_contracts(cfg: E2EConfig) -> None:
    log.step("Checking AWS provider contracts...")
    provider_contracts = load_provider_contracts_or_raise(cfg)
    expected = {
        ("storage", "implementation"): "storage.aws_s3",
        ("metadata_database", "implementation"): "metadata_database.aws_rds_postgresql",
        ("catalog", "implementation"): "catalog.aws_glue",
        ("catalog", "catalog_type"): "glue",
        ("artifacts", "implementation"): "artifacts.aws_ecr_and_s3",
    }
    for path, expected_value in expected.items():
        value: Any = provider_contracts
        for key in path:
            value = value[key]
        if value != expected_value:
            raise E2EError(f"provider_contracts.{'.'.join(path)} expected {expected_value!r}, got {value!r}")


def check_aws_storage_and_glue(cfg: E2EConfig) -> None:
    log.step("Checking S3 artifact bucket and Glue catalog databases...")
    provider_contracts = load_provider_contracts_or_raise(cfg)
    bucket = provider_contracts["artifact_bucket"]["bucket_name"]
    region = aws_stack_region(cfg)
    _run(["aws", "s3api", "head-bucket", "--bucket", bucket], capture=True)
    for database in glue_database_names(provider_contracts):
        _run(["aws", "glue", "get-database", "--region", region, "--name", database], capture=True)


def glue_database_names(provider_contracts: Mapping[str, Any]) -> set[str]:
    catalog = provider_contracts["catalog"]
    schema_names = set(catalog.get("catalog_schema_names") or [ns["name"] for ns in catalog["catalog_namespaces"]])
    database_names = set(catalog.get("glue_database_names") or [])
    missing = EXPECTED_GLUE_SCHEMAS.difference(schema_names)
    if missing:
        raise E2EError(f"catalog contract missing Glue schema names: {sorted(missing)}")
    missing_databases = EXPECTED_GLUE_SCHEMAS.difference(database_names)
    if missing_databases:
        raise E2EError(f"catalog contract missing Glue database names: {sorted(missing_databases)}")
    return database_names


def load_provider_contracts_or_raise(cfg: E2EConfig) -> Mapping[str, Any]:
    provider_contracts = contracts.load_provider_contracts(str(cfg.contract_terraform_dir))
    if provider_contracts is None:
        raise E2EError(f"could not load provider_contracts from {cfg.contract_terraform_dir}")
    return provider_contracts


def aws_stack_region(cfg: E2EConfig) -> str:
    if cfg.foundation_terraform_dir is not None:
        return terraform_output(cfg.foundation_terraform_dir, "aws_region")
    if cfg.aws_region:
        return cfg.aws_region
    if os.environ.get("AWS_REGION"):
        return os.environ["AWS_REGION"]
    raise E2EError("AWS e2e requires a stack region from Terraform output or AWS_REGION.")


def terraform_output(terraform_dir: Path | None, name: str) -> str:
    if terraform_dir is None:
        raise E2EError(f"cannot read Terraform output {name}: no Terraform directory configured.")
    return _run(["terraform", f"-chdir={terraform_dir}", "output", "-raw", name], capture=True).strip()


def kubectl(cfg: E2EConfig, args: list[str], *, capture: bool = False) -> str:
    return _run(["kubectl", "--context", cfg.kube_context, *args], capture=capture)


def _run(args: list[str], *, capture: bool = False) -> str:
    result = subprocess.run(args, capture_output=capture, text=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr if capture else "") or ""
        raise E2EError(f"{' '.join(args)} failed: {detail.strip()}")
    return result.stdout if capture else ""


def _run_retry(args: list[str], *, capture: bool = False, attempts: int = 3, delay: float = 2.0) -> str:
    last_error: E2EError | None = None
    for attempt in range(1, attempts + 1):
        try:
            return _run(args, capture=capture)
        except E2EError as exc:
            last_error = exc
            if attempt == attempts:
                break
            time.sleep(delay)
    if last_error is None:
        raise E2EError(f"{' '.join(args)} failed.")
    raise last_error
