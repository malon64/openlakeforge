"""Dagster product-job launch/poll and repository-location discovery."""

from __future__ import annotations

import json
import os
from collections.abc import Sequence

from openlakeforge_domain import Product

from olf import k8s, log
from olf.clients.base import ServiceClientError
from olf.clients.dagster import DagsterClient, DagsterHTTPError, DagsterTransientError  # noqa: F401 - re-exported
from olf.e2e._health import _bounded_pod_diagnostics
from olf.e2e._shell import E2EConfig, E2EError, kubectl, terraform_output, terraform_output_json

DAGSTER_JOB_TIMEOUT_SECONDS = 1800


def launch_and_poll_dagster_jobs(cfg: E2EConfig, *, products: Sequence[Product] | None = None) -> None:
    log.step("Launching and polling Dagster product jobs...")
    assert cfg.dagster_local_port is not None
    webserver_service_name = terraform_output(cfg.contract_terraform_dir, "dagster_webserver_service_name")
    log_path = f"/tmp/openlakeforge-{cfg.env}-dagster-port-forward.log"
    with k8s.port_forward(
        webserver_service_name,
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
            except ServiceClientError as exc:
                diagnostics = _bounded_pod_diagnostics(
                    cfg,
                    [webserver_service_name, *expected_user_code_pods(cfg, location_names)],
                )
                raise E2EError(f"{exc}\nDagster diagnostics:\n{diagnostics}") from exc
            log.info(f"{job}: launched ({run_id})")
            try:
                client.poll(job, run_id, timeout_seconds=timeout_seconds)
            except ServiceClientError as exc:
                raise E2EError(str(exc)) from exc


def dagster_release_name(cfg: E2EConfig) -> str:
    """This stage's Dagster Helm release name (`app.kubernetes.io/instance`).

    Derived from the webserver service name Terraform already outputs
    (`{release_name}-dagster-webserver`, modules/orchestration/dagster)
    rather than assumed as the bare "dagster" local defaults to - the cloud
    POC roots pass `release_name = "dagster-<stage>"` explicitly.
    """
    return terraform_output(cfg.contract_terraform_dir, "dagster_webserver_service_name").removesuffix(
        "-dagster-webserver"
    )


def expected_user_code_pods(cfg: E2EConfig, location_names: Sequence[str]) -> list[str]:
    """Discover configured user-code deployments for bounded failure diagnostics."""
    try:
        raw = kubectl(cfg, ["get", "pods", "-n", cfg.namespace, "-o", "json"], capture=True)
        payload = json.loads(raw)
    except (E2EError, json.JSONDecodeError):
        return []
    release_name = dagster_release_name(cfg)
    return [
        str(item.get("metadata", {}).get("name"))
        for item in payload.get("items", [])
        if item.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/name") == "dagster-user-deployments"
        and item.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/instance") == release_name
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
