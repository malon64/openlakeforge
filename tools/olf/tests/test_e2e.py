from pathlib import Path
from typing import Any

import pytest

from olf import e2e


def cfg(tmp_path: Path, env: e2e.Environment = "local", suite: e2e.Suite = "full") -> e2e.E2EConfig:
    return e2e.E2EConfig(
        env=env,
        suite=suite,
        namespace="lakehouse",
        kube_context="kind-openlakeforge-local",
        repo_root=tmp_path,
        foundation_terraform_dir=tmp_path / "foundation",
        contract_terraform_dir=tmp_path / "contract",
        aws_region="eu-west-1" if env == "aws" else None,
    )


def test_unhealthy_pod_messages_accepts_ready_running_and_succeeded() -> None:
    payload = {
        "items": [
            {
                "metadata": {"name": "service"},
                "status": {"phase": "Running", "containerStatuses": [{"name": "app", "ready": True}]},
            },
            {"metadata": {"name": "bootstrap"}, "status": {"phase": "Succeeded"}},
        ]
    }

    assert e2e.unhealthy_pod_messages(payload) == []


def test_unhealthy_pod_messages_reports_unready_and_failed_pods() -> None:
    payload = {
        "items": [
            {
                "metadata": {"name": "service"},
                "status": {"phase": "Running", "containerStatuses": [{"name": "app", "ready": False}]},
            },
            {"metadata": {"name": "bad-job"}, "status": {"phase": "Failed"}},
        ]
    }

    assert e2e.unhealthy_pod_messages(payload) == [
        "service: Running but containers not ready: app",
        "bad-job: Failed",
    ]


def test_check_pods_ready_retries_until_pods_are_ready(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payloads = [
        '{"items":[{"metadata":{"name":"service"},"status":{"phase":"Running","containerStatuses":[{"name":"app","ready":false}]}}]}',
        '{"items":[{"metadata":{"name":"service"},"status":{"phase":"Running","containerStatuses":[{"name":"app","ready":true}]}}]}',
    ]
    monkeypatch.setattr(e2e, "kubectl", lambda _cfg, _args, capture=False: payloads.pop(0))
    monkeypatch.setattr(e2e.time, "sleep", lambda _delay: None)

    e2e.check_pods_ready(cfg(tmp_path))
    assert payloads == []


def test_prepare_kube_context_reuses_existing_aws_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    commands: list[list[str]] = []

    def run(args: list[str], *, capture: bool = False) -> str:
        commands.append(args)
        return ""

    monkeypatch.setattr(e2e, "_run", run)

    e2e.prepare_kube_context(cfg(tmp_path, env="aws", suite="smoke"))

    assert ["kubectl", "cluster-info", "--context", "kind-openlakeforge-local"] in commands
    assert ["kubectl", "config", "use-context", "kind-openlakeforge-local"] in commands
    assert not any(command[:3] == ["aws", "eks", "update-kubeconfig"] for command in commands)


def test_prepare_kube_context_selects_existing_local_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    commands: list[list[str]] = []

    def run(args: list[str], *, capture: bool = False) -> str:
        commands.append(args)
        return ""

    monkeypatch.setattr(e2e, "_run", run)

    e2e.prepare_kube_context(cfg(tmp_path))

    assert ["kubectl", "cluster-info", "--context", "kind-openlakeforge-local"] in commands
    assert ["kubectl", "config", "use-context", "kind-openlakeforge-local"] in commands


def test_prepare_kube_context_updates_aws_context_when_existing_context_is_unusable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    commands: list[list[str]] = []

    def run(args: list[str], *, capture: bool = False) -> str:
        commands.append(args)
        if args[:2] == ["kubectl", "cluster-info"] and len(commands) == 1:
            raise e2e.E2EError("context missing")
        return ""

    monkeypatch.setattr(e2e, "_run", run)
    monkeypatch.setattr(e2e.time, "sleep", lambda _delay: None)
    monkeypatch.setattr(
        e2e,
        "terraform_output",
        lambda _dir, name: {
            "aws_region": "eu-west-1",
            "cluster_name": "limited-eks-openlakeforge-poc",
        }[name],
    )

    e2e.prepare_kube_context(cfg(tmp_path, env="aws", suite="smoke"))

    assert [
        "aws",
        "eks",
        "update-kubeconfig",
        "--region",
        "eu-west-1",
        "--name",
        "limited-eks-openlakeforge-poc",
        "--alias",
        "kind-openlakeforge-local",
    ] in commands


def test_parse_trino_scalar_returns_last_non_empty_line() -> None:
    assert e2e.parse_trino_scalar("\n15\r\n") == "15"


def test_assert_scalar_equals_reports_mismatch() -> None:
    with pytest.raises(e2e.E2EError, match="expected Gold mart count 9, got 8"):
        e2e.assert_scalar_equals("8", "9", "Gold mart count")


def test_superset_dashboard_assertion_accepts_slug_or_title_match() -> None:
    e2e.assert_superset_dashboards(
        [
            {"slug": "sales-order-revenue", "dashboard_title": "Sales Order Revenue"},
            {"slug": "different-slug", "dashboard_title": "Sales Customer Health"},
            {
                "slug": "supply-chain-inventory-reliability",
                "dashboard_title": "Supply Chain Inventory Reliability",
            },
        ]
    )


def test_superset_dashboard_assertion_reports_missing_dashboard() -> None:
    with pytest.raises(e2e.E2EError, match="missing Superset dashboards"):
        e2e.assert_superset_dashboards([])


def test_openmetadata_data_product_candidates_try_short_and_domain_names() -> None:
    seen: list[str] = []

    class Client(e2e.OpenMetadataE2EClient):
        def _request(
            self,
            method: str,
            path: str,
            token: str,
            *,
            payload: dict[str, Any] | None = None,
            ok_statuses: tuple[int, ...] = (200,),
        ) -> tuple[int, dict[str, Any]]:
            seen.append(path)
            if path.endswith("/sales.sales_order_revenue"):
                return 200, {}
            return 404, {}

    client = Client("http://openmetadata")

    assert client._first_existing_data_product(("sales_order_revenue", "sales.sales_order_revenue"), "token") == (
        "sales.sales_order_revenue"
    )
    assert seen == [
        "/api/v1/dataProducts/name/sales_order_revenue",
        "/api/v1/dataProducts/name/sales.sales_order_revenue",
    ]


def test_dagster_repository_discovery_finds_pipeline_location() -> None:
    client = e2e.DagsterClient(
        "http://dagster/graphql",
        request_json=lambda _query, _variables=None: {
            "data": {
                "workspaceOrError": {
                    "__typename": "Workspace",
                    "locationEntries": [
                        {
                            "name": "sales-dagster",
                            "locationOrLoadError": {
                                "__typename": "RepositoryLocation",
                                "repositories": [
                                    {
                                        "name": "__repository__",
                                        "pipelines": [{"name": "sales_order_revenue_pipeline"}],
                                    }
                                ],
                            },
                        }
                    ],
                }
            }
        },
    )

    assert client.discover_repository("sales_order_revenue_pipeline") == ("sales-dagster", "__repository__")


def test_dagster_repository_discovery_finds_job_location() -> None:
    client = e2e.DagsterClient(
        "http://dagster/graphql",
        request_json=lambda _query, _variables=None: {
            "data": {
                "workspaceOrError": {
                    "__typename": "Workspace",
                    "locationEntries": [
                        {
                            "name": "sales-dagster",
                            "locationOrLoadError": {
                                "__typename": "RepositoryLocation",
                                "repositories": [
                                    {
                                        "name": "__repository__",
                                        "jobs": [{"name": "sales_order_revenue_pipeline"}],
                                    }
                                ],
                            },
                        }
                    ],
                }
            }
        },
    )

    assert client.discover_repository("sales_order_revenue_pipeline") == ("sales-dagster", "__repository__")


def test_dagster_repository_discovery_raises_on_workspace_error() -> None:
    client = e2e.DagsterClient(
        "http://dagster/graphql",
        request_json=lambda _query, _variables=None: {"errors": [{"message": "workspace unavailable"}]},
    )

    with pytest.raises(e2e.E2EError, match="workspace unavailable"):
        client.discover_repository("supply_chain_inventory_reliability_pipeline")


def test_dagster_repository_discovery_reports_load_errors() -> None:
    client = e2e.DagsterClient(
        "http://dagster/graphql",
        request_json=lambda _query, _variables=None: {
            "data": {
                "workspaceOrError": {
                    "__typename": "Workspace",
                    "locationEntries": [
                        {
                            "name": "sales-dagster",
                            "locationOrLoadError": {
                                "__typename": "PythonError",
                                "message": "user code unreachable",
                            },
                        }
                    ],
                }
            }
        },
    )

    with pytest.raises(e2e.E2EError, match="user code unreachable"):
        client.discover_repository("sales_order_revenue_pipeline")


def test_dagster_launch_uses_discovered_repository() -> None:
    calls: list[dict[str, Any]] = []

    def request_json(query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        calls.append({"query": query, "variables": variables})
        if "query Workspace" in query:
            return {
                "data": {
                    "workspaceOrError": {
                        "__typename": "Workspace",
                        "locationEntries": [
                            {
                                "name": "sales-dagster",
                                "locationOrLoadError": {
                                    "__typename": "RepositoryLocation",
                                    "repositories": [
                                        {
                                            "name": "__repository__",
                                            "pipelines": [{"name": "sales_order_revenue_pipeline"}],
                                        }
                                    ],
                                },
                            }
                        ],
                    }
                }
            }
        return {"data": {"launchRun": {"__typename": "LaunchRunSuccess", "run": {"runId": "run-1"}}}}

    client = e2e.DagsterClient("http://dagster/graphql", request_json=request_json)

    assert client.launch("sales_order_revenue_pipeline") == "run-1"
    selector = calls[-1]["variables"]["executionParams"]["selector"]
    assert selector == {
        "repositoryLocationName": "sales-dagster",
        "repositoryName": "__repository__",
        "pipelineName": "sales_order_revenue_pipeline",
    }


def test_dagster_wait_for_repository_retries_until_job_is_available() -> None:
    responses = [
        {
            "data": {
                "workspaceOrError": {
                    "__typename": "Workspace",
                    "locationEntries": [
                        {
                            "name": "sales-dagster",
                            "locationOrLoadError": {
                                "__typename": "PythonError",
                                "message": "user code unreachable",
                            },
                        }
                    ],
                }
            }
        },
        {
            "data": {
                "reloadRepositoryLocation": {
                    "__typename": "WorkspaceLocationEntry",
                    "name": "sales-dagster",
                }
            }
        },
        {
            "data": {
                "workspaceOrError": {
                    "__typename": "Workspace",
                    "locationEntries": [
                        {
                            "name": "sales-dagster",
                            "locationOrLoadError": {
                                "__typename": "RepositoryLocation",
                                "repositories": [
                                    {
                                        "name": "__repository__",
                                        "jobs": [{"name": "sales_order_revenue_pipeline"}],
                                    }
                                ],
                            },
                        }
                    ],
                }
            }
        },
    ]

    def request_json(_query: str, _variables: dict[str, Any] | None = None) -> dict[str, Any]:
        return responses.pop(0)

    client = e2e.DagsterClient("http://dagster/graphql", request_json=request_json)

    assert client.wait_for_repository("sales_order_revenue_pipeline", timeout_seconds=1, delay=0) == (
        "sales-dagster",
        "__repository__",
    )


def test_expected_repository_location_name_uses_domain_prefix() -> None:
    assert e2e.expected_repository_location_name("sales_order_revenue_pipeline") == "sales-dagster"
    assert (
        e2e.expected_repository_location_name("supply_chain_inventory_reliability_pipeline")
        == "supply-chain-dagster"
    )


def test_dagster_poll_reports_failure() -> None:
    client = e2e.DagsterClient(
        "http://dagster/graphql",
        request_json=lambda _query, _variables=None: {
            "data": {"runOrError": {"__typename": "Run", "status": "FAILURE"}}
        },
    )

    with pytest.raises(e2e.E2EError, match="ended with FAILURE"):
        client.poll("sales_order_revenue_pipeline", "run-1", attempts=1, delay=0)


def test_dagster_poll_retries_transient_graphql_errors() -> None:
    responses: list[Exception | dict[str, Any]] = [
        e2e.DagsterTransientError("read timeout"),
        {"data": {"runOrError": {"__typename": "Run", "status": "SUCCESS"}}},
    ]

    def request_json(_query: str, _variables: dict[str, Any] | None = None) -> dict[str, Any]:
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    client = e2e.DagsterClient("http://dagster/graphql", request_json=request_json)

    client.poll("sales_order_revenue_pipeline", "run-1", attempts=2, delay=0)
    assert responses == []


def test_dagster_poll_times_out_quickly_for_non_terminal_runs() -> None:
    client = e2e.DagsterClient(
        "http://dagster/graphql",
        request_json=lambda _query, _variables=None: {
            "data": {"runOrError": {"__typename": "Run", "status": "STARTED"}}
        },
    )

    with pytest.raises(e2e.E2EError, match="did not finish within 1800 seconds"):
        client.poll("sales_order_revenue_pipeline", "run-1", attempts=1, delay=0)


def test_launch_and_poll_dagster_jobs_defaults_to_previous_shell_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    timeouts: list[int] = []

    class Client:
        def __init__(self, _url: str) -> None:
            pass

        def launch(self, _job: str) -> str:
            return "run-1"

        def poll(self, _job: str, _run_id: str, *, timeout_seconds: int) -> None:
            timeouts.append(timeout_seconds)

    monkeypatch.delenv("DAGSTER_JOB_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setattr(e2e, "DagsterClient", Client)
    monkeypatch.setattr(e2e.k8s, "http_wait", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        e2e.k8s,
        "port_forward",
        lambda *_args, **_kwargs: __import__("contextlib").nullcontext(),
    )

    local_cfg = e2e.E2EConfig(
        env="local",
        suite="full",
        namespace="lakehouse",
        kube_context="kind-openlakeforge-local",
        repo_root=tmp_path,
        foundation_terraform_dir=tmp_path / "foundation",
        contract_terraform_dir=tmp_path / "contract",
        dagster_local_port=13000,
    )

    e2e.launch_and_poll_dagster_jobs(local_cfg)

    assert timeouts == [e2e.DAGSTER_JOB_TIMEOUT_SECONDS] * len(e2e.PRODUCT_JOBS)


def test_glue_database_names_requires_expected_schema_and_database_names() -> None:
    contracts = {
        "catalog": {
            "catalog_schema_names": sorted(e2e.EXPECTED_GLUE_SCHEMAS),
            "glue_database_names": sorted(e2e.EXPECTED_GLUE_SCHEMAS),
        }
    }

    assert e2e.glue_database_names(contracts) == e2e.EXPECTED_GLUE_SCHEMAS


def test_glue_database_names_reports_missing_database() -> None:
    contracts = {
        "catalog": {
            "catalog_schema_names": sorted(e2e.EXPECTED_GLUE_SCHEMAS),
            "glue_database_names": [],
        }
    }

    with pytest.raises(e2e.E2EError, match="missing Glue database names"):
        e2e.glue_database_names(contracts)


def test_aws_provider_contract_smoke_check(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider_contracts = {
        "storage": {"implementation": "storage.aws_s3"},
        "metadata_database": {"implementation": "metadata_database.aws_rds_postgresql"},
        "catalog": {"implementation": "catalog.aws_glue", "catalog_type": "glue"},
        "artifacts": {"implementation": "artifacts.aws_ecr_and_s3"},
    }
    monkeypatch.setattr(e2e, "load_provider_contracts_or_raise", lambda _cfg: provider_contracts)

    e2e.check_aws_provider_contracts(cfg(tmp_path, env="aws", suite="smoke"))


def test_aws_storage_and_glue_smoke_check_uses_bucket_and_databases(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    provider_contracts = {
        "artifact_bucket": {"bucket_name": "openlakeforge-ops"},
        "catalog": {
            "catalog_schema_names": sorted(e2e.EXPECTED_GLUE_SCHEMAS),
            "glue_database_names": sorted(e2e.EXPECTED_GLUE_SCHEMAS),
        },
    }
    commands: list[list[str]] = []
    monkeypatch.setattr(e2e, "load_provider_contracts_or_raise", lambda _cfg: provider_contracts)
    monkeypatch.setattr(e2e, "terraform_output", lambda _dir, name: "eu-central-1" if name == "aws_region" else "")
    monkeypatch.setattr(e2e, "_run", lambda args, capture=False: commands.append(args) or "")

    e2e.check_aws_storage_and_glue(cfg(tmp_path, env="aws", suite="smoke"))

    assert ["aws", "s3api", "head-bucket", "--bucket", "openlakeforge-ops"] in commands
    glue_commands = [command for command in commands if command[:3] == ["aws", "glue", "get-database"]]
    assert len(glue_commands) == len(e2e.EXPECTED_GLUE_SCHEMAS)
    assert all(command[4] == "eu-central-1" for command in glue_commands)


def test_aws_stack_region_prefers_foundation_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setattr(e2e, "terraform_output", lambda _dir, name: "eu-central-1" if name == "aws_region" else "")

    assert e2e.aws_stack_region(cfg(tmp_path, env="aws", suite="smoke")) == "eu-central-1"


def test_check_ops_artifacts_uses_configured_bucket_for_local(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bucket_waits: list[tuple[str, str]] = []
    artifact_checks: list[tuple[str, str]] = []

    class FakeS3Client:
        pass

    local_cfg = e2e.E2EConfig(
        env="local",
        suite="full",
        namespace="lakehouse",
        kube_context="kind-openlakeforge-local",
        repo_root=tmp_path,
        foundation_terraform_dir=tmp_path / "foundation",
        contract_terraform_dir=tmp_path / "contract",
        seaweedfs_local_port=19000,
    )

    monkeypatch.setattr(e2e, "trigger_log_archive_job", lambda _cfg: None)
    monkeypatch.setattr(
        e2e,
        "load_provider_contracts_or_raise",
        lambda _cfg: {"artifact_bucket": {"bucket_name": "custom-ops-bucket"}},
    )
    monkeypatch.setattr(e2e.k8s, "secret_value", lambda *_args, **_kwargs: "secret")
    monkeypatch.setattr(
        e2e.k8s,
        "port_forward",
        lambda *_args, **_kwargs: __import__("contextlib").nullcontext(),
    )
    monkeypatch.setattr(e2e.boto3, "client", lambda *_args, **_kwargs: FakeS3Client())
    monkeypatch.setattr(
        e2e,
        "wait_for_bucket",
        lambda _client, bucket, endpoint: bucket_waits.append((bucket, endpoint)),
    )
    monkeypatch.setattr(
        e2e,
        "assert_ops_artifacts",
        lambda _client, bucket, namespace: artifact_checks.append((bucket, namespace)),
    )

    e2e.check_ops_artifacts(local_cfg)

    assert bucket_waits == [("custom-ops-bucket", "http://127.0.0.1:19000")]
    assert artifact_checks == [("custom-ops-bucket", "lakehouse")]


def test_run_retry_retries_transient_command_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0

    def run(args: list[str], *, capture: bool = False) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise e2e.E2EError("temporary failure")
        return "ok"

    monkeypatch.setattr(e2e, "_run", run)
    monkeypatch.setattr(e2e.time, "sleep", lambda _delay: None)

    assert e2e._run_retry(["kubectl", "cluster-info"], capture=True, attempts=2, delay=0) == "ok"
    assert attempts == 2
