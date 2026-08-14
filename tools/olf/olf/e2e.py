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
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import boto3
import requests
import yaml
from botocore.config import Config

from olf import contracts, k8s, log, revision, superset
from olf.inventory import DomainInventory, Product, inventory_for

Environment = Literal["local", "azure", "aws"]
Suite = Literal["full", "smoke"]
Layer = Literal["governance", "analytics"]

# The one artifact prefix that is genuinely platform-level, not per-product:
# Dagster's own compute-log archive path. Per-product prefixes (Floe reports,
# dbt artifacts, Floe manifests) come from the domain inventory instead.
ARTIFACT_PREFIXES = ("logs/dagster/compute/",)

DAGSTER_JOB_TIMEOUT_SECONDS = 1800
READINESS_ATTEMPTS = 60
READINESS_DELAY_SECONDS = 5
DIAGNOSTIC_LOG_LINES = 80
LAUNCH_RETRY_ATTEMPTS = 4
LAUNCH_RETRY_DELAY_SECONDS = 3
KUBECTL_READ_RETRY_ATTEMPTS = 4
KUBECTL_READ_RETRY_DELAY_SECONDS = 2
POLARIS_POD_SELECTOR = "app.kubernetes.io/name=polaris,app.kubernetes.io/instance=polaris"
POLARIS_RESTART_TIMEOUT_SECONDS = 300
REQUIRED_PLATFORM_JOB_TYPES = frozenset(
    {
        "bucket-bootstrap",
        "catalog-bootstrap",
        "catalog-metastore-bootstrap",
        "governance-bootstrap",
        "postgresql-bootstrap",
        "rds-bootstrap",
    }
)
TRANSIENT_KUBECTL_ERROR_MARKERS = (
    "tls handshake timeout",
    "i/o timeout",
    "connection reset by peer",
    "http2: client connection lost",
    "the server is currently unable to handle the request",
)


class E2EError(RuntimeError):
    pass


class DagsterTransientError(E2EError):
    pass


class DagsterHTTPError(E2EError):
    """A non-transient Dagster HTTP response (normally a client error)."""


@dataclass(frozen=True)
class FullAssertion:
    """One full-suite assertion and the optional platform layer it needs."""

    label: str
    check: Callable[[E2EConfig], None]
    layer: Layer | None = None


def _bounded_pod_diagnostics(cfg: E2EConfig, names: list[str]) -> str:
    lines: list[str] = []
    for name in names[:10]:
        try:
            output = kubectl(
                cfg,
                ["logs", "-n", cfg.namespace, f"pod/{name}", "--all-containers", f"--tail={DIAGNOSTIC_LOG_LINES}"],
                capture=True,
            )
        except E2EError as exc:
            output = f"unable to collect logs: {exc}"
        lines.append(f"{name}:\n{output[-6000:]}")
    return "\n".join(lines)


def _bounded_job_diagnostics(cfg: E2EConfig, names: list[str]) -> str:
    """Collect only a small tail from the named Jobs."""
    diagnostics: list[str] = []
    for job_name in names[:10]:
        try:
            output = kubectl(
                cfg,
                ["logs", "-n", cfg.namespace, f"job/{job_name}", f"--tail={DIAGNOSTIC_LOG_LINES}"],
                capture=True,
            )
        except E2EError as exc:
            output = f"unable to collect logs: {exc}"
        diagnostics.append(f"{job_name}:\n{output[-6000:]}")
    return "\n".join(diagnostics)


@dataclass(frozen=True)
class E2EConfig:
    env: Environment
    suite: Suite
    namespace: str
    kube_context: str
    repo_root: Path
    foundation_terraform_dir: Path | None
    contract_terraform_dir: Path
    inventory: DomainInventory
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
        if cfg.env == "aws":
            run_smoke(cfg)
        run_full(cfg)
    log.info(f"{cfg.env.capitalize()} OpenLakeForge {cfg.suite} e2e validation passed.")


def default_suite(_env: Environment) -> Suite:
    return "full"


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
    inventory = inventory_for(root)
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
            inventory=inventory,
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
            inventory=inventory,
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
            inventory=inventory,
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
    kubeconfig = configure_kubeconfig(cfg)
    if cfg.env == "local" and kube_context_is_ready(cfg.kube_context):
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
                "--file",
                str(kubeconfig),
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
                "--kubeconfig",
                str(kubeconfig),
                "--alias",
                cfg.kube_context,
            ]
        )
    _run_retry(["kubectl", "cluster-info", "--context", cfg.kube_context], capture=True, attempts=6, delay=5)


def configure_kubeconfig(cfg: E2EConfig) -> Path:
    provider_override = os.environ.get(f"{cfg.env.upper()}_KUBECONFIG_PATH")
    configured_path = os.environ.get("KUBECONFIG") or provider_override
    kubeconfig = (
        Path(configured_path).expanduser()
        if configured_path
        else cfg.repo_root / ".tmp/kubeconfigs" / f"{cfg.env}.yaml"
    )
    kubeconfig = kubeconfig.resolve()
    kubeconfig.parent.mkdir(parents=True, exist_ok=True)
    os.environ["KUBECONFIG"] = str(kubeconfig)
    return kubeconfig


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
    check_catalog_namespaces(cfg)
    if cfg.env == "local":
        product = cfg.inventory.default_product
        log.step(f"Running smoke path for descriptor-selected product {product.id}...")
        launch_and_poll_dagster_jobs(cfg, products=(product,))
        check_trino_product_tables_and_marts(cfg, product)


def run_full(cfg: E2EConfig) -> None:
    enabled_layers = configured_layers(cfg)
    skipped: list[str] = []
    for assertion in full_assertions(cfg):
        if assertion.layer is not None and not enabled_layers[assertion.layer]:
            skipped.append(assertion.label)
            log.info(f"Skipping {assertion.label}: {assertion.layer} layer is disabled.")
            continue
        assertion.check(cfg)
    if skipped:
        log.info("Skipped e2e assertions: " + ", ".join(skipped))


def full_assertions(cfg: E2EConfig) -> tuple[FullAssertion, ...]:
    """Return the full-suite assertion inventory for the deployed profile."""
    assertions = [
        FullAssertion("Polaris namespaces", check_catalog_namespaces),
        FullAssertion("Dagster product pipelines", launch_and_poll_dagster_jobs),
        FullAssertion("Silver and Gold tables", check_trino_tables_and_marts),
    ]
    if cfg.env == "local":
        assertions.append(FullAssertion("Polaris restart recovery", check_polaris_restart_recovery))
    assertions.extend(
        [
            FullAssertion("Superset dashboards", check_superset_dashboards, layer="analytics"),
            FullAssertion("OpenMetadata governance assets", check_openmetadata_assets, layer="governance"),
            FullAssertion("ops bucket artifacts", check_ops_artifacts),
        ]
    )
    return tuple(assertions)


def configured_layers(cfg: E2EConfig) -> dict[Layer, bool]:
    """Read enabled platform layers once from the environment contract."""
    provider_contracts = load_provider_contracts_or_raise(cfg)
    layers = {
        "governance": provider_contracts.get("governance") or {},
        "analytics": provider_contracts.get("reporting") or {},
    }
    enabled: dict[Layer, bool] = {}
    for layer, contract in layers.items():
        value = contract.get("enabled", True)
        if not isinstance(value, bool):
            raise E2EError(f"provider_contracts.{layer}.enabled must be a boolean.")
        enabled[layer] = value
    return enabled


def check_pods_ready(cfg: E2EConfig) -> None:
    log.step("Checking pod health...")
    service_bad: list[str] = []
    job_bad: list[str] = []
    last_error: E2EError | None = None
    reported_warnings: set[str] = set()
    for _ in range(READINESS_ATTEMPTS):
        try:
            pod_payload = json.loads(kubectl(cfg, ["get", "pods", "-n", cfg.namespace, "-o", "json"], capture=True))
            job_payload = json.loads(kubectl(cfg, ["get", "jobs", "-n", cfg.namespace, "-o", "json"], capture=True))
        except E2EError as exc:
            last_error = exc
            time.sleep(5)
            continue
        service_bad = classify_pod_health(pod_payload)
        job_bad, warned = classify_job_health(job_payload)
        new_warnings = [message for message in warned if message not in reported_warnings]
        reported_warnings.update(new_warnings)
        for message in new_warnings:
            log.warn(message)
        if new_warnings:
            warning_job_names = [message.split(":", 1)[0] for message in new_warnings]
            diagnostics = _bounded_job_diagnostics(cfg, warning_job_names)
            if diagnostics:
                log.warn(f"non-blocking Job diagnostics:\n{diagnostics}")
        if not service_bad and not job_bad:
            return
        time.sleep(READINESS_DELAY_SECONDS)
    if last_error is not None and not service_bad and not job_bad:
        raise last_error
    diagnostics: list[str] = []
    if service_bad:
        diagnostics.append(_bounded_pod_diagnostics(cfg, [message.split(":", 1)[0] for message in service_bad]))
    if job_bad:
        diagnostics.append(_bounded_job_diagnostics(cfg, [message.split(":", 1)[0] for message in job_bad]))
    raise E2EError(
        "unhealthy required services:\n"
        + "\n".join([*service_bad, *job_bad])
        + "\nDiagnostics:\n"
        + "\n".join(diagnostics)
    )


def classify_pod_health(payload: Mapping[str, Any]) -> list[str]:
    """Return blocking health failures for long-lived service pods only."""
    bad: list[str] = []
    for item in payload.get("items", []):
        owner_kinds = {str(owner.get("kind")) for owner in item.get("metadata", {}).get("ownerReferences", [])}
        if "Job" not in owner_kinds:
            bad.extend(unhealthy_pod_messages({"items": [item]}))
    return bad


def classify_job_health(payload: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """Return blocking bootstrap Job failures and warnings for non-blocking Jobs."""
    bad: list[str] = []
    warned: list[str] = []
    for item in payload.get("items", []):
        metadata = item.get("metadata", {})
        name = str(metadata.get("name", "<unnamed>"))
        labels = item.get("spec", {}).get("template", {}).get("metadata", {}).get("labels", {})
        job_type = labels.get("openlakeforge.io/job", "")
        state = job_state(item)
        if job_type in REQUIRED_PLATFORM_JOB_TYPES:
            if state != "Complete":
                bad.append(f"{name}: {state}")
            continue
        if state != "Complete":
            warned.append(f"{name}: non-blocking Job is {state}; continuing E2E readiness")
    return bad, warned


def job_state(item: Mapping[str, Any]) -> str:
    """Return the terminal or current state represented by a Kubernetes Job."""
    conditions = item.get("status", {}).get("conditions", [])
    completed = any(
        condition.get("type") == "Complete" and condition.get("status") == "True" for condition in conditions
    )
    if completed:
        return "Complete"
    failed = any(
        condition.get("type") == "Failed" and condition.get("status") == "True" for condition in conditions
    )
    if failed:
        return "Failed"
    if item.get("status", {}).get("active"):
        return "Running"
    return "Pending"


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
    catalogs = trino_query(cfg, "SHOW CATALOGS")
    if "iceberg" not in set(catalogs.splitlines()):
        raise E2EError("Trino did not expose the iceberg catalog.")


def check_catalog_namespaces(cfg: E2EConfig) -> None:
    """Verify Polaris exposes every descriptor-derived namespace through Trino."""
    log.step("Checking Polaris namespaces through Trino...")
    namespaces = set(trino_query(cfg, "SHOW SCHEMAS FROM iceberg").splitlines())
    expected = cfg.inventory.silver_namespace_names | cfg.inventory.gold_namespace_names
    missing = sorted(expected - namespaces)
    if missing:
        raise E2EError("Polaris is missing descriptor-derived namespaces: " + ", ".join(missing))


def _schema_in_list(schema_names: frozenset[str]) -> str:
    return ", ".join(f"'{name}'" for name in sorted(schema_names))


def check_trino_tables_and_marts(cfg: E2EConfig) -> None:
    check_trino_catalog(cfg)
    log.step("Checking Silver and Gold table counts...")
    silver_count = trino_scalar(
        cfg,
        "SELECT count(*) FROM iceberg.information_schema.tables "
        f"WHERE table_schema IN ({_schema_in_list(cfg.inventory.silver_namespace_names)})",
    )
    gold_count = trino_scalar(
        cfg,
        "SELECT count(*) FROM iceberg.information_schema.tables "
        f"WHERE table_schema IN ({_schema_in_list(cfg.inventory.gold_namespace_names)})",
    )
    assert_scalar_equals(silver_count, str(cfg.inventory.silver_table_count), "Silver table count")
    assert_scalar_equals(gold_count, str(cfg.inventory.gold_table_count), "Gold mart count")

    for mart in cfg.inventory.gold_mart_names:
        count = trino_scalar(cfg, f"SELECT count(*) FROM iceberg.{mart}")
        if int(count) <= 0:
            raise E2EError(f"expected iceberg.{mart} to contain rows, got {count}")


def check_trino_product_tables_and_marts(cfg: E2EConfig, product: Product) -> None:
    """Verify the selected smoke product reached populated Silver and Gold tables."""
    check_trino_catalog(cfg)
    log.step(f"Checking Silver and Gold tables for {product.id}...")
    silver_count = trino_scalar(
        cfg,
        "SELECT count(*) FROM iceberg.information_schema.tables "
        f"WHERE table_schema = '{product.silver_namespace}'",
    )
    gold_count = trino_scalar(
        cfg,
        "SELECT count(*) FROM iceberg.information_schema.tables "
        f"WHERE table_schema = '{product.gold_namespace}'",
    )
    assert_scalar_equals(silver_count, str(len(product.silver_tables)), f"{product.id} Silver table count")
    assert_scalar_equals(gold_count, str(len(product.gold_tables)), f"{product.id} Gold mart count")
    for mart in product.gold_mart_names:
        count = trino_scalar(cfg, f"SELECT count(*) FROM iceberg.{mart}")
        if int(count) <= 0:
            raise E2EError(f"expected iceberg.{mart} to contain rows, got {count}")


def check_polaris_restart_recovery(cfg: E2EConfig) -> None:
    """Verify that Polaris keeps table identity after its pod is recreated."""
    log.step("Checking Polaris restart recovery...")
    pod_name = kubectl(
        cfg,
        [
            "get",
            "pods",
            "-n",
            cfg.namespace,
            "-l",
            POLARIS_POD_SELECTOR,
            "-o",
            "jsonpath={.items[0].metadata.name}",
        ],
        capture=True,
    ).strip()
    if not pod_name:
        raise E2EError("could not find the Polaris pod to restart.")

    kubectl(cfg, ["delete", "pod", pod_name, "-n", cfg.namespace])
    kubectl(
        cfg,
        [
            "wait",
            "--for=condition=Ready",
            "pod",
            "-n",
            cfg.namespace,
            "-l",
            POLARIS_POD_SELECTOR,
            f"--timeout={POLARIS_RESTART_TIMEOUT_SECONDS}s",
        ],
    )
    check_trino_tables_and_marts(cfg)


def trino_scalar(cfg: E2EConfig, sql: str) -> str:
    return parse_trino_scalar(trino_query(cfg, sql))


def trino_query(cfg: E2EConfig, sql: str) -> str:
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
        retry_transient=True,
    )
    return output


def parse_trino_scalar(output: str) -> str:
    values = [line.strip().replace("\r", "") for line in output.splitlines() if line.strip()]
    if not values:
        raise E2EError("Trino query returned no scalar value.")
    return values[-1]


def assert_scalar_equals(actual: str, expected: str, label: str) -> None:
    if actual != expected:
        raise E2EError(f"expected {label} {expected}, got {actual}")


def launch_and_poll_dagster_jobs(cfg: E2EConfig, *, products: Sequence[Product] | None = None) -> None:
    log.step("Launching and polling Dagster product jobs...")
    assert cfg.dagster_local_port is not None
    log_path = f"/tmp/openlakeforge-{cfg.env}-dagster-port-forward.log"
    with k8s.port_forward(
        "dagster-dagster-webserver",
        80,
        cfg.namespace,
        local_port=cfg.dagster_local_port,
        log_path=log_path,
        kube_context=cfg.kube_context,
    ):
        base_url = f"http://127.0.0.1:{cfg.dagster_local_port}"
        if not k8s.http_wait(f"{base_url}/server_info", attempts=90, delay=2):
            raise E2EError("Dagster endpoint did not become reachable.")
        location_names = expected_repository_location_names(cfg)
        client = DagsterClient(f"{base_url}/graphql", expected_location_names=location_names)
        timeout_seconds = int(os.environ.get("DAGSTER_JOB_TIMEOUT_SECONDS", str(DAGSTER_JOB_TIMEOUT_SECONDS)))
        for product in products or cfg.inventory.products:
            job = product.job_name
            try:
                run_id = client.launch(job)
            except E2EError as exc:
                diagnostics = _bounded_pod_diagnostics(
                    cfg,
                    ["dagster-dagster-webserver", *expected_user_code_pods(cfg, location_names)],
                )
                raise E2EError(f"{exc}\nDagster diagnostics:\n{diagnostics}") from exc
            log.info(f"{job}: launched ({run_id})")
            client.poll(job, run_id, timeout_seconds=timeout_seconds)


def expected_user_code_pods(cfg: E2EConfig, location_names: Sequence[str]) -> list[str]:
    """Discover configured user-code deployments for bounded failure diagnostics."""
    try:
        raw = kubectl(cfg, ["get", "pods", "-n", cfg.namespace, "-o", "json"], capture=True)
        payload = json.loads(raw)
    except (E2EError, json.JSONDecodeError):
        return []
    return [
        str(item.get("metadata", {}).get("name"))
        for item in payload.get("items", [])
        if item.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/name") == "dagster-user-deployments"
        and item.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/instance") == "dagster"
        and item.get("metadata", {}).get("labels", {}).get("deployment") in location_names
    ]


def expected_repository_location_names(cfg: E2EConfig) -> list[str]:
    """Read Dagster locations from the deployed environment contract."""
    location_names = terraform_output_json(cfg.contract_terraform_dir, "dagster_code_location_names")
    if (
        not isinstance(location_names, list)
        or not location_names
        or any(not isinstance(location_name, str) or not location_name for location_name in location_names)
        or len(set(location_names)) != len(location_names)
    ):
        raise E2EError("Terraform output dagster_code_location_names must be a non-empty list of unique names.")
    return location_names


class DagsterClient:
    def __init__(
        self,
        graphql_url: str,
        *,
        expected_location_names: Sequence[str] = (),
        request_json: Callable[[str, Mapping[str, Any] | None], Mapping[str, Any]] | None = None,
    ) -> None:
        self.graphql_url = graphql_url
        self.expected_location_names = tuple(expected_location_names)
        self._request_json = request_json or self._requests_graphql

    def launch(self, job_name: str) -> str:
        location_name, repository_name = self.wait_for_repository(job_name)
        launch_key = f"openlakeforge-e2e-{job_name}-{uuid.uuid4().hex}"
        variables = {
            "executionParams": {
                "selector": {
                    "repositoryLocationName": location_name,
                    "repositoryName": repository_name,
                    "pipelineName": job_name,
                },
                "runConfigData": {},
                "mode": "default",
                "executionMetadata": {
                    "tags": [{"key": "openlakeforge/e2e-key", "value": launch_key}],
                },
            }
        }
        last_error: DagsterTransientError | None = None
        for attempt in range(LAUNCH_RETRY_ATTEMPTS):
            try:
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
                    variables,
                )["launchRun"]
                break
            except DagsterTransientError as exc:
                last_error = exc
                existing = self.find_run_by_tag(launch_key)
                if existing:
                    return existing
                if attempt + 1 == LAUNCH_RETRY_ATTEMPTS:
                    raise E2EError(
                        f"Dagster launch for {job_name} failed after {LAUNCH_RETRY_ATTEMPTS} attempts: {exc}"
                    ) from exc
                time.sleep(LAUNCH_RETRY_DELAY_SECONDS)
        else:  # pragma: no cover - loop always breaks or raises
            raise E2EError(f"Dagster launch failed: {last_error}")
        if result["__typename"] != "LaunchRunSuccess":
            raise E2EError(f"failed to launch {job_name}: {json.dumps(result, indent=2)}")
        return result["run"]["runId"]

    def find_run_by_tag(self, launch_key: str) -> str | None:
        """Find a run created before an ambiguous HTTP response."""
        try:
            result = self.graphql(
                """
                query ExistingRun($key: String!) {
                  runsOrError(filter: {tags: [{key: "openlakeforge/e2e-key", value: $key}]}, limit: 1) {
                    __typename
                    ... on Runs { results { runId } }
                  }
                }
                """,
                {"key": launch_key},
            )["runsOrError"]
            runs = result.get("results", [])
            return str(runs[0]["runId"]) if runs else None
        except E2EError:
            return None

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
                self.try_reload_repository_locations()
                time.sleep(delay)
        detail = f": {last_error}" if last_error else ""
        raise E2EError(
            f"Dagster repository for {job_name} did not become ready "
            f"within {timeout_seconds} seconds{detail}. Required code location may be unavailable; "
            "inspect Dagster webserver and user-code logs."
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

    def try_reload_repository_locations(self) -> None:
        for location_name in self.expected_location_names:
            self.try_reload_repository_location(location_name)

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
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status is not None and 500 <= status < 600:
                raise DagsterTransientError(f"Dagster GraphQL HTTP {status}: {exc}") from exc
            raise DagsterHTTPError(f"Dagster GraphQL HTTP {status}: {exc}") from exc
        except (requests.ConnectionError, requests.Timeout) as exc:
            raise DagsterTransientError(f"Dagster GraphQL request failed: {exc}") from exc
        return response.json()


def check_superset_dashboards(cfg: E2EConfig) -> None:
    log.step("Checking Superset report imports...")
    assert cfg.superset_local_port is not None
    log_path = f"/tmp/openlakeforge-{cfg.env}-superset-port-forward.log"
    with k8s.port_forward(
        "superset",
        8088,
        cfg.namespace,
        local_port=cfg.superset_local_port,
        log_path=log_path,
        kube_context=cfg.kube_context,
    ):
        base_url = f"http://127.0.0.1:{cfg.superset_local_port}"
        if not k8s.http_wait(f"{base_url}/health", attempts=90, delay=2):
            raise E2EError("Superset endpoint did not become reachable.")
        dashboards = SupersetClient(base_url).dashboards()
    assert_superset_dashboards(dashboards, discovered_dashboards(cfg))


def discovered_dashboards(cfg: E2EConfig) -> dict[str, str]:
    """Read the slug/title of every dashboard each product actually exports.

    A product's descriptor cannot say what its dashboard is titled or slugged
    as, or how many it exports — that identity lives only in the
    source-controlled Superset export YAML under
    ``<report_source_dir>/dashboards/*.yaml`` (or ``.yml``, both of which
    ``superset.build_report_bundle`` packages), which is what actually gets
    imported. Reading it here (rather than inventing slug/title from
    asset_prefix/displayName) is what lets a product export a differently
    named or multiple dashboards without failing this check.
    """
    expected: dict[str, str] = {}
    for product in cfg.inventory.products:
        report_dir = cfg.repo_root / product.report_source_dir
        dashboard_files = superset.discover_dashboard_files(report_dir)
        if not dashboard_files:
            raise E2EError(
                f"{report_dir / 'dashboards'}: product {product.asset_prefix!r} exports no Superset dashboards"
            )
        for dashboard_file in dashboard_files:
            document = yaml.safe_load(dashboard_file.read_text())
            slug = document.get("slug") if isinstance(document, Mapping) else None
            title = document.get("dashboard_title") if isinstance(document, Mapping) else None
            if not slug or not title:
                raise E2EError(f"{dashboard_file}: missing slug or dashboard_title")
            expected[slug] = title
    return expected


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


def assert_superset_dashboards(dashboards: list[Mapping[str, Any]], expected: Mapping[str, str]) -> None:
    by_slug = {dashboard.get("slug"): dashboard.get("dashboard_title") for dashboard in dashboards}
    titles = {dashboard.get("dashboard_title") for dashboard in dashboards}
    missing = [
        f"{slug} ({title})" for slug, title in expected.items() if by_slug.get(slug) != title and title not in titles
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
        kube_context=cfg.kube_context,
    ):
        base_url = f"http://127.0.0.1:{cfg.openmetadata_local_port}"
        if not k8s.http_wait(f"{base_url}/api/v1/system/config/jwks", attempts=90, delay=2):
            raise E2EError("OpenMetadata endpoint did not become reachable.")
        OpenMetadataE2EClient(base_url, cfg.inventory).assert_assets()


class OpenMetadataE2EClient:
    def __init__(self, base_url: str, inventory: DomainInventory) -> None:
        self.base_url = base_url.rstrip("/")
        self.inventory = inventory

    def assert_assets(self) -> None:
        token = self._login()
        for domain in self.inventory.domain_names:
            self._request("GET", f"/api/v1/domains/name/{domain}", token)
        for product, candidates in self.inventory.openmetadata_data_products.items():
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
    deployed_revision = deployed_floe_manifest_revision(cfg)
    provider_contracts = load_provider_contracts_or_raise(cfg)
    bucket = provider_contracts["artifact_bucket"]["bucket_name"]
    if cfg.env == "aws":
        client = boto3.client("s3", region_name=aws_stack_region(cfg))
        assert_ops_artifacts(client, bucket, cfg.namespace, cfg.inventory, deployed_revision)
        return

    assert cfg.seaweedfs_local_port is not None
    access_key_id = k8s.secret_value(
        "seaweedfs-s3-creds",
        "AWS_ACCESS_KEY_ID",
        cfg.namespace,
        kube_context=cfg.kube_context,
    )
    secret_access_key = k8s.secret_value(
        "seaweedfs-s3-creds",
        "AWS_SECRET_ACCESS_KEY",
        cfg.namespace,
        kube_context=cfg.kube_context,
    )
    log_path = f"/tmp/openlakeforge-{cfg.env}-seaweedfs-s3-port-forward.log"
    with k8s.port_forward(
        "seaweedfs-s3",
        8333,
        cfg.namespace,
        local_port=cfg.seaweedfs_local_port,
        log_path=log_path,
        kube_context=cfg.kube_context,
    ):
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
        assert_ops_artifacts(client, bucket, cfg.namespace, cfg.inventory, deployed_revision)


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


def deployed_floe_manifest_revision(cfg: E2EConfig) -> str:
    """Read the immutable revision baked into each running Dagster code pod."""
    location_names = expected_repository_location_names(cfg)
    pods = expected_user_code_pods(cfg, location_names)
    if not pods:
        raise E2EError("no running Dagster user-code pods were found to identify the deployed Floe revision.")

    revisions: dict[str, str] = {}
    for pod in pods:
        value = kubectl(
            cfg,
            [
                "exec",
                "-n",
                cfg.namespace,
                pod,
                "--",
                "printenv",
                "OPENLAKEFORGE_FLOE_MANIFEST_REVISION_BUILT",
            ],
            capture=True,
        ).strip()
        if not value:
            raise E2EError(f"Dagster user-code pod {pod} has no built Floe manifest revision.")
        revisions[pod] = value

    unique_revisions = set(revisions.values())
    if len(unique_revisions) != 1:
        details = ", ".join(f"{pod}={value}" for pod, value in sorted(revisions.items()))
        raise E2EError(f"Dagster user-code pods disagree on the built Floe manifest revision: {details}.")
    return unique_revisions.pop()


def assert_ops_artifacts(
    client: Any,
    bucket: str,
    namespace: str,
    inventory: DomainInventory,
    deployed_revision: str,
) -> None:
    assert_immutable_floe_manifests(client, bucket, inventory, deployed_revision)
    product_prefixes = tuple(
        prefix
        for product in inventory.products
        for prefix in (product.artifact_prefixes.floe_report_prefix, product.artifact_prefixes.dbt_artifact_prefix)
    )
    for prefix in (*product_prefixes, *ARTIFACT_PREFIXES, f"logs/k8s/namespace={namespace}/"):
        require_s3_prefix(client, bucket, prefix)


def assert_immutable_floe_manifests(
    client: Any, bucket: str, inventory: DomainInventory, deployed_revision: str
) -> None:
    try:
        manifest = revision.verify(client, bucket, deployed_revision)
    except revision.RevisionError as exc:
        raise E2EError(f"failed to verify deployed immutable Floe revision {deployed_revision}: {exc}") from exc
    expected_manifest_keys = set(inventory.manifest_keys)
    missing = expected_manifest_keys.difference(manifest.entries)
    if missing:
        raise E2EError(
            f"deployed immutable Floe revision {deployed_revision} does not contain every "
            f"descriptor-discovered product manifest: {', '.join(sorted(missing))}."
        )


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
    """Assert the S3 artifact bucket and every expected Glue database exist.

    Glue database lifecycle moved to Phase 2 (ADR 0022), so the provider
    contract no longer publishes a namespace/database list to check against --
    `olf catalog sync-namespaces` is what is under test here, and the only
    source of truth for "did it work" is AWS itself.
    """
    log.step("Checking S3 artifact bucket and Glue catalog databases...")
    provider_contracts = load_provider_contracts_or_raise(cfg)
    bucket = provider_contracts["artifact_bucket"]["bucket_name"]
    region = aws_stack_region(cfg)
    expected_schemas = cfg.inventory.silver_namespace_names | cfg.inventory.gold_namespace_names
    _run(["aws", "s3api", "head-bucket", "--bucket", bucket], capture=True)
    for database in sorted(expected_schemas):
        _run(["aws", "glue", "get-database", "--region", region, "--name", database], capture=True)


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


def terraform_output_json(terraform_dir: Path | None, name: str) -> Any:
    if terraform_dir is None:
        raise E2EError(f"cannot read Terraform output {name}: no Terraform directory configured.")
    raw = _run(["terraform", f"-chdir={terraform_dir}", "output", "-json", name], capture=True)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise E2EError(f"Terraform output {name} was not valid JSON.") from exc


def kubectl(
    cfg: E2EConfig,
    args: list[str],
    *,
    capture: bool = False,
    retry_transient: bool = False,
) -> str:
    command = ["kubectl", "--context", cfg.kube_context, *args]
    if retry_transient:
        return _run_retry_transient_kubectl(command, capture=capture)
    return _run(command, capture=capture)


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


def _run_retry_transient_kubectl(
    args: list[str],
    *,
    capture: bool = False,
    attempts: int = KUBECTL_READ_RETRY_ATTEMPTS,
    delay: float = KUBECTL_READ_RETRY_DELAY_SECONDS,
) -> str:
    for attempt in range(1, attempts + 1):
        try:
            return _run(args, capture=capture)
        except E2EError as exc:
            if attempt == attempts or not is_transient_kubectl_error(exc):
                raise
            log.warn(
                f"Transient Kubernetes API error during read-only Trino probe "
                f"(attempt {attempt}/{attempts}); retrying..."
            )
            time.sleep(delay)
    raise E2EError(f"{' '.join(args)} failed.")  # pragma: no cover


def is_transient_kubectl_error(error: Exception) -> bool:
    message = str(error).lower()
    return any(marker in message for marker in TRANSIENT_KUBECTL_ERROR_MARKERS)
