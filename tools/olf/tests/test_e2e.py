import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from openlakeforge_domain import inventory_for, load_domain_inventory

from olf import e2e
from olf.clients import dagster as dagster_client_module
from olf.clients.base import ServiceClientError
from olf.clients.openmetadata import OpenMetadataError

REPO_ROOT = Path(__file__).resolve().parents[3]
INVENTORY = inventory_for(REPO_ROOT)


def _write_two_product_fixture(root: Path) -> None:
    """A minimal two-product, single-domain descriptor for isolation tests."""
    path = root / "domains" / "widgets" / "domain.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(
        """\
apiVersion: openlakeforge.io/v1alpha2
kind: Domain
name: widgets
displayName: Widgets
description: Widgets domain fixture.
status: planned
data_products:
  - id: alpha
    name: widgets_alpha
    displayName: Widgets Alpha
    description: Alpha product.
    status: planned
    asset_prefix: widgets_alpha
    bronze:
      - name: source
        path: s3://lakehouse-bronze/widgets/alpha/source
    silver_tables:
      tables:
        - name: source
    gold_tables:
      tables:
        - name: mart_alpha_summary
  - id: beta
    name: widgets_beta
    displayName: Widgets Beta
    description: Beta product.
    status: planned
    asset_prefix: widgets_beta
    bronze:
      - name: source
        path: s3://lakehouse-bronze/widgets/beta/source
    silver_tables:
      tables:
        - name: source
    gold_tables:
      tables:
        - name: mart_beta_summary
""",
        encoding="utf-8",
    )


def cfg(tmp_path: Path, env: e2e.Environment = "local", suite: e2e.Suite = "full") -> e2e.E2EConfig:
    return e2e.E2EConfig(
        env=env,
        suite=suite,
        namespace="lakehouse",
        kube_context="kind-openlakeforge-local",
        repo_root=tmp_path,
        foundation_terraform_dir=tmp_path / "foundation",
        contract_terraform_dir=tmp_path / "contract",
        inventory=INVENTORY,
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


def test_classify_pod_health_ignores_job_attempt_pods() -> None:
    assert e2e.classify_pod_health(
        {
            "items": [
                {
                    "metadata": {
                        "name": "om-job-pod",
                        "ownerReferences": [{"kind": "Job", "name": "om-job-aws-glue-metadata-ingestion"}],
                        "labels": {"job-name": "om-job-aws-glue-metadata-ingestion"},
                    },
                    "status": {"phase": "Failed"},
                }
            ]
        }
    ) == []


def test_classify_job_health_accepts_completed_job_with_failed_retry_pod() -> None:
    bad, warned = e2e.classify_job_health(
        {
            "items": [
                {
                    "metadata": {"name": "postgresql-bootstrap"},
                    "spec": {
                        "template": {
                            "metadata": {"labels": {"openlakeforge.io/readiness": "required"}}
                        }
                    },
                    "status": {
                        "failed": 2,
                        "succeeded": 1,
                        "conditions": [{"type": "Complete", "status": "True"}],
                    },
                }
            ]
        }
    )
    assert warned == []
    assert bad == []


def test_classify_job_health_blocks_failed_platform_bootstrap_job() -> None:
    bad, warned = e2e.classify_job_health(
        {
            "items": [
                {
                    "metadata": {"name": "polaris-bootstrap"},
                    "spec": {
                        "template": {
                            "metadata": {"labels": {"openlakeforge.io/readiness": "required"}}
                        }
                    },
                    "status": {"conditions": [{"type": "Failed", "status": "True"}]},
                }
            ]
        }
    )

    assert warned == []
    assert bad == ["polaris-bootstrap: Failed"]


def test_classify_job_health_warns_for_historical_cron_job_failure() -> None:
    bad, warned = e2e.classify_job_health(
        {
            "items": [
                {
                    "metadata": {"name": "openmetadata-polaris-refresh-old"},
                    "spec": {"template": {"metadata": {"labels": {"openlakeforge.io/job": "catalog-refresh"}}}},
                    "status": {"conditions": [{"type": "Failed", "status": "True"}]},
                }
            ]
        }
    )

    assert bad == []
    assert warned == [
        "openmetadata-polaris-refresh-old: non-blocking Job is Failed; continuing E2E readiness"
    ]


def test_classify_job_health_blocks_active_suite_job() -> None:
    bad, warned = e2e.classify_job_health(
        {
            "items": [
                {
                    "metadata": {
                        "name": "dagster-run-current",
                        "labels": {"dagster/job": "sales_order_revenue_pipeline"},
                    },
                    "status": {"active": 1},
                }
            ]
        },
        suite_jobs=("sales_order_revenue_pipeline",),
    )

    assert bad == ["dagster-run-current: Running"]
    assert warned == []


def test_classify_job_health_warns_for_historical_suite_job_failure() -> None:
    bad, warned = e2e.classify_job_health(
        {
            "items": [
                {
                    "metadata": {
                        "name": "dagster-run-old",
                        "labels": {"dagster/job": "sales_order_revenue_pipeline"},
                    },
                    "status": {"conditions": [{"type": "Failed", "status": "True"}]},
                }
            ]
        },
        suite_jobs=("sales_order_revenue_pipeline",),
    )

    assert bad == []
    assert warned == ["dagster-run-old: non-blocking Job is Failed; continuing E2E readiness"]


def test_bounded_pod_diagnostics_describes_a_pod_when_logs_are_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

    def fake_kubectl(_cfg: e2e.E2EConfig, args: list[str], *, capture: bool = False) -> str:
        calls.append(args)
        if args[0] == "logs":
            raise e2e.E2EError("container is waiting to start")
        return "Events:\n  Failed to pull image"

    monkeypatch.setattr(e2e, "kubectl", fake_kubectl)

    diagnostics = e2e._bounded_pod_diagnostics(cfg(tmp_path), ["dagster-pod"])

    assert calls == [
        ["logs", "-n", "lakehouse", "pod/dagster-pod", "--all-containers", "--tail=80"],
        ["describe", "pod", "-n", "lakehouse", "dagster-pod"],
    ]
    assert "Failed to pull image" in diagnostics


def test_check_pods_ready_retries_until_pods_are_ready(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pod_payloads = iter(
        [
            '{"items":[{"metadata":{"name":"service"},"status":{"phase":"Running","containerStatuses":[{"name":"app","ready":false}]}}]}',
            '{"items":[{"metadata":{"name":"service"},"status":{"phase":"Running","containerStatuses":[{"name":"app","ready":true}]}}]}',
        ]
    )

    def kubectl(_cfg, args, capture=False):  # noqa: ANN001, ARG001 - test double
        return next(pod_payloads) if args[1] == "pods" else '{"items":[]}'

    monkeypatch.setattr(e2e, "kubectl", kubectl)
    monkeypatch.setattr(e2e.time, "sleep", lambda _delay: None)

    e2e.check_pods_ready(cfg(tmp_path))


def test_check_pods_ready_uses_job_completion_not_retry_pod_phase(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []
    pod_payload = json.dumps(
        {
            "items": [
                {
                    "metadata": {
                        "name": "postgresql-bootstrap-retry",
                        "ownerReferences": [{"kind": "Job", "name": "postgresql-bootstrap"}],
                    },
                    "status": {"phase": "Failed"},
                }
            ]
        }
    )
    job_payload = json.dumps(
        {
            "items": [
                {
                    "metadata": {"name": "postgresql-bootstrap"},
                    "spec": {
                        "template": {
                            "metadata": {"labels": {"openlakeforge.io/readiness": "required"}}
                        }
                    },
                    "status": {"conditions": [{"type": "Complete", "status": "True"}]},
                }
            ]
        }
    )

    def kubectl(_cfg, args, capture=False):  # noqa: ANN001, ARG001 - test double
        calls.append(args)
        return pod_payload if args[1] == "pods" else job_payload

    monkeypatch.setattr(e2e, "kubectl", kubectl)

    e2e.check_pods_ready(cfg(tmp_path))

    assert calls == [
        ["get", "pods", "-n", "lakehouse", "-o", "json"],
        ["get", "jobs", "-n", "lakehouse", "-o", "json"],
    ]


def test_job_diagnostics_use_configured_namespace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(e2e, "kubectl", lambda _cfg, args, capture=False: calls.append(args) or "job log")

    assert e2e._bounded_job_diagnostics(cfg(tmp_path), ["polaris-bootstrap"]) == "polaris-bootstrap:\njob log"
    assert calls == [["logs", "-n", "lakehouse", "job/polaris-bootstrap", "--tail=80"]]


def test_pod_diagnostics_use_configured_namespace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(e2e, "kubectl", lambda _cfg, args, capture=False: calls.append(args) or "pod log")

    assert e2e._bounded_pod_diagnostics(cfg(tmp_path), ["trino-coordinator"]) == "trino-coordinator:\npod log"
    assert calls == [["logs", "-n", "lakehouse", "pod/trino-coordinator", "--all-containers", "--tail=80"]]


@pytest.mark.parametrize("env", ["local", "azure", "aws"])
def test_default_suite_is_full(env: e2e.Environment) -> None:
    assert e2e.default_suite(env) == "full"


def test_aws_default_suite_includes_smoke_and_full_checks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[str] = []

    monkeypatch.setattr(e2e, "check_commands", lambda _cfg: None)
    monkeypatch.setattr(e2e, "prepare_kube_context", lambda _cfg: None)
    monkeypatch.setattr(e2e, "check_pods_ready", lambda _cfg: None)
    monkeypatch.setattr(e2e, "run_smoke", lambda _cfg: calls.append("smoke"))
    monkeypatch.setattr(e2e, "run_full", lambda _cfg: calls.append("full"))

    e2e.run("aws", repo_root=REPO_ROOT)

    assert calls == ["smoke", "full"]


def test_aws_explicit_smoke_suite_skips_full_checks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[str] = []

    monkeypatch.setattr(e2e, "check_commands", lambda _cfg: None)
    monkeypatch.setattr(e2e, "prepare_kube_context", lambda _cfg: None)
    monkeypatch.setattr(e2e, "check_pods_ready", lambda _cfg: None)
    monkeypatch.setattr(e2e, "run_smoke", lambda _cfg: calls.append("smoke"))
    monkeypatch.setattr(e2e, "run_full", lambda _cfg: calls.append("full"))

    e2e.run("aws", suite="smoke", repo_root=REPO_ROOT)

    assert calls == ["smoke"]


def test_local_smoke_runs_only_the_descriptor_default_product(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[object] = []
    local_cfg = cfg(tmp_path, suite="smoke")

    monkeypatch.setattr(e2e, "check_trino_catalog", lambda _cfg: calls.append("catalog"))
    monkeypatch.setattr(e2e, "check_catalog_namespaces", lambda _cfg: calls.append("namespaces"))
    monkeypatch.setattr(
        e2e,
        "launch_and_poll_dagster_jobs",
        lambda _cfg, *, products=None: calls.append(tuple(product.id for product in products or ())),
    )
    monkeypatch.setattr(
        e2e,
        "check_trino_product_tables_and_marts",
        lambda _cfg, product: calls.append(("tables", product.id)),
    )

    e2e.run_smoke(local_cfg)

    assert calls == ["catalog", "namespaces", (INVENTORY.default_product.id,), ("tables", INVENTORY.default_product.id)]


def test_check_catalog_namespaces_reports_missing_descriptor_namespace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    available = INVENTORY.silver_namespace_names | (INVENTORY.gold_namespace_names - {"sales_order_revenue_gold"})
    monkeypatch.setattr(e2e, "trino_query", lambda _cfg, _sql: "\n".join(sorted(available)))

    with pytest.raises(e2e.E2EError, match="sales_order_revenue_gold"):
        e2e.check_catalog_namespaces(cfg(tmp_path))


def test_smoke_product_table_check_fails_for_an_empty_gold_mart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    product = INVENTORY.default_product
    values = iter((str(len(product.silver_tables)), str(len(product.gold_tables)), "0"))
    monkeypatch.setattr(e2e, "check_trino_catalog", lambda _cfg: None)
    monkeypatch.setattr(e2e, "trino_scalar", lambda _cfg, _sql: next(values))

    with pytest.raises(e2e.E2EError, match="expected iceberg"):
        e2e.check_trino_product_tables_and_marts(cfg(tmp_path, suite="smoke"), product)


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ("local", ["namespaces", "jobs", "tables", "recovery", "superset", "openmetadata", "artifacts"]),
        ("azure", ["namespaces", "jobs", "tables", "superset", "openmetadata", "artifacts"]),
        ("aws", ["namespaces", "jobs", "tables", "superset", "openmetadata", "artifacts"]),
    ],
)
def test_run_full_only_restarts_polaris_for_local(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, env: e2e.Environment, expected: list[str]
) -> None:
    calls: list[str] = []

    monkeypatch.setattr(e2e, "check_catalog_namespaces", lambda _cfg: calls.append("namespaces"))
    monkeypatch.setattr(e2e, "launch_and_poll_dagster_jobs", lambda _cfg: calls.append("jobs"))
    monkeypatch.setattr(e2e, "check_trino_tables_and_marts", lambda _cfg: calls.append("tables"))
    monkeypatch.setattr(e2e, "check_polaris_restart_recovery", lambda _cfg: calls.append("recovery"))
    monkeypatch.setattr(e2e, "check_superset_dashboards", lambda _cfg: calls.append("superset"))
    monkeypatch.setattr(e2e, "check_openmetadata_assets", lambda _cfg: calls.append("openmetadata"))
    monkeypatch.setattr(e2e, "check_ops_artifacts", lambda _cfg: calls.append("artifacts"))
    monkeypatch.setattr(e2e, "configured_layers", lambda _cfg: {"governance": True, "analytics": True})

    e2e.run_full(cfg(tmp_path, env=env))

    assert calls == expected


def test_run_full_skips_disabled_layer_assertions_and_reports_them(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []
    messages: list[str] = []

    monkeypatch.setattr(e2e, "check_catalog_namespaces", lambda _cfg: calls.append("namespaces"))
    monkeypatch.setattr(e2e, "launch_and_poll_dagster_jobs", lambda _cfg: calls.append("jobs"))
    monkeypatch.setattr(e2e, "check_trino_tables_and_marts", lambda _cfg: calls.append("tables"))
    monkeypatch.setattr(e2e, "check_polaris_restart_recovery", lambda _cfg: calls.append("recovery"))
    monkeypatch.setattr(e2e, "check_superset_dashboards", lambda _cfg: calls.append("superset"))
    monkeypatch.setattr(e2e, "check_openmetadata_assets", lambda _cfg: calls.append("openmetadata"))
    monkeypatch.setattr(e2e, "check_ops_artifacts", lambda _cfg: calls.append("artifacts"))
    monkeypatch.setattr(e2e, "configured_layers", lambda _cfg: {"governance": False, "analytics": False})
    monkeypatch.setattr(e2e.log, "info", messages.append)

    e2e.run_full(cfg(tmp_path))

    assert calls == ["namespaces", "jobs", "tables", "recovery", "artifacts"]
    assert "Skipped e2e assertions: Superset dashboards, OpenMetadata governance assets" in messages


def test_configured_layers_reads_contract_flags(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        e2e,
        "load_provider_contracts_or_raise",
        lambda _cfg: {"governance": {"enabled": False}, "reporting": {"enabled": False}},
    )

    assert e2e.configured_layers(cfg(tmp_path)) == {"governance": False, "analytics": False}


def test_prepare_kube_context_refreshes_existing_aws_context(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    commands: list[list[str]] = []
    kubeconfig = tmp_path / "aws.yaml"
    monkeypatch.setenv("KUBECONFIG", str(kubeconfig))

    def run(args: list[str], *, capture: bool = False) -> str:
        commands.append(args)
        return ""

    monkeypatch.setattr(e2e, "_run", run)
    monkeypatch.setattr(
        e2e,
        "terraform_output",
        lambda _dir, name: {
            "aws_region": "eu-west-1",
            "cluster_name": "limited-eks-openlakeforge-poc",
        }[name],
    )

    e2e.prepare_kube_context(cfg(tmp_path, env="aws", suite="smoke"))

    assert ["kubectl", "cluster-info", "--context", "kind-openlakeforge-local"] in commands
    assert [
        "aws",
        "eks",
        "update-kubeconfig",
        "--region",
        "eu-west-1",
        "--name",
        "limited-eks-openlakeforge-poc",
        "--kubeconfig",
        str(kubeconfig),
        "--alias",
        "kind-openlakeforge-local",
    ] in commands


@pytest.mark.parametrize(
    ("env", "expected_command_prefix", "terraform_outputs"),
    [
        (
            "azure",
            ["az", "aks", "get-credentials"],
            {"resource_group_name": "rg-sandbox", "cluster_name": "aks-openlakeforge-poc"},
        ),
        (
            "aws",
            ["aws", "eks", "update-kubeconfig"],
            {"aws_region": "eu-west-1", "cluster_name": "limited-eks-openlakeforge-poc"},
        ),
    ],
)
def test_prepare_kube_context_uses_provider_default_for_direct_cloud_runs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    env: e2e.Environment,
    expected_command_prefix: list[str],
    terraform_outputs: dict[str, str],
) -> None:
    commands: list[list[str]] = []
    monkeypatch.delenv("KUBECONFIG", raising=False)
    monkeypatch.delenv(f"{env.upper()}_KUBECONFIG_PATH", raising=False)
    monkeypatch.setattr(e2e, "_run", lambda args, capture=False: commands.append(args) or "")
    monkeypatch.setattr(e2e, "terraform_output", lambda _dir, name: terraform_outputs[name])

    e2e.prepare_kube_context(cfg(tmp_path, env=env, suite="smoke"))

    expected_kubeconfig = tmp_path / ".tmp/kubeconfigs" / f"{env}.yaml"
    cloud_command = next(command for command in commands if command[:3] == expected_command_prefix)
    assert str(expected_kubeconfig) in cloud_command
    assert Path(os.environ["KUBECONFIG"]) == expected_kubeconfig
    assert expected_kubeconfig.parent.is_dir()


def test_prepare_kube_context_selects_existing_local_context(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def run(args: list[str], *, capture: bool = False) -> str:
        commands.append(args)
        return ""

    monkeypatch.setattr(e2e, "_run", run)

    e2e.prepare_kube_context(cfg(tmp_path))

    assert ["kubectl", "cluster-info", "--context", "kind-openlakeforge-local"] in commands
    assert not any(command[:3] == ["kubectl", "config", "use-context"] for command in commands)


def test_prepare_kube_context_updates_aws_context_when_existing_context_is_unusable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    commands: list[list[str]] = []
    kubeconfig = tmp_path / "aws.yaml"
    monkeypatch.setenv("KUBECONFIG", str(kubeconfig))

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
        "--kubeconfig",
        str(kubeconfig),
        "--alias",
        "kind-openlakeforge-local",
    ] in commands


def test_parse_trino_scalar_returns_last_non_empty_line() -> None:
    assert e2e.parse_trino_scalar("\n15\r\n") == "15"


def test_trino_scalar_requests_transient_kubectl_retries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []

    def kubectl(
        _cfg: e2e.E2EConfig,
        args: list[str],
        *,
        capture: bool = False,
        retry_transient: bool = False,
    ) -> str:
        calls.append(
            {
                "args": args,
                "capture": capture,
                "retry_transient": retry_transient,
            }
        )
        return "6\n"

    monkeypatch.setattr(e2e, "kubectl", kubectl)

    assert e2e.trino_scalar(cfg(tmp_path), "SELECT count(*) FROM iceberg.test.table") == "6"
    assert calls[0]["capture"] is True
    assert calls[0]["retry_transient"] is True


def test_check_polaris_restart_recovery_waits_then_rechecks_trino(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    commands: list[list[str]] = []
    checks: list[str] = []

    def kubectl(
        _cfg: e2e.E2EConfig,
        args: list[str],
        *,
        capture: bool = False,
        retry_transient: bool = False,
    ) -> str:
        commands.append(args)
        assert not retry_transient
        if args[0] == "get":
            assert capture
            return "polaris-6b8d7d9f5c-abcde"
        assert not capture
        return ""

    monkeypatch.setattr(e2e, "kubectl", kubectl)
    monkeypatch.setattr(e2e, "check_trino_tables_and_marts", lambda _cfg: checks.append("trino"))

    e2e.check_polaris_restart_recovery(cfg(tmp_path))

    assert commands == [
        [
            "get",
            "pods",
            "-n",
            "lakehouse",
            "-l",
            "app.kubernetes.io/name=polaris,app.kubernetes.io/instance=polaris",
            "-o",
            "jsonpath={.items[0].metadata.name}",
        ],
        ["delete", "pod", "polaris-6b8d7d9f5c-abcde", "-n", "lakehouse"],
        [
            "wait",
            "--for=condition=Ready",
            "pod",
            "-n",
            "lakehouse",
            "-l",
            "app.kubernetes.io/name=polaris,app.kubernetes.io/instance=polaris",
            "--timeout=300s",
        ],
    ]
    assert checks == ["trino"]


def test_check_polaris_restart_recovery_requires_a_pod(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(e2e, "kubectl", lambda *_args, **_kwargs: "")

    with pytest.raises(e2e.E2EError, match="could not find the Polaris pod"):
        e2e.check_polaris_restart_recovery(cfg(tmp_path))


def test_assert_scalar_equals_reports_mismatch() -> None:
    with pytest.raises(e2e.E2EError, match="expected Gold mart count 9, got 8"):
        e2e.assert_scalar_equals("8", "9", "Gold mart count")


def test_openmetadata_data_product_candidates_try_short_and_domain_names() -> None:
    seen: list[str] = []

    def request(method: str, path: str, **_kwargs) -> dict[str, Any]:
        seen.append(path)
        if path.endswith("/sales.sales_order_revenue"):
            return {}
        raise OpenMetadataError(f"{method} {path} failed with HTTP 404: not found")

    client = e2e.OpenMetadataClient("http://openmetadata")
    client.request = request

    assert e2e._first_existing_data_product(client, ("sales_order_revenue", "sales.sales_order_revenue")) == (
        "sales.sales_order_revenue"
    )
    assert seen == [
        "/api/v1/dataProducts/name/sales_order_revenue",
        "/api/v1/dataProducts/name/sales.sales_order_revenue",
    ]


def test_dagster_repository_discovery_finds_all_pipeline_locations_in_merged_location() -> None:
    client = e2e.DagsterClient(
        "http://dagster/graphql",
        request_json=lambda _query, _variables=None: {
            "data": {
                "workspaceOrError": {
                    "__typename": "Workspace",
                    "locationEntries": [
                        {
                            "name": "openlakeforge-dagster",
                            "locationOrLoadError": {
                                "__typename": "RepositoryLocation",
                                "repositories": [
                                    {
                                        "name": "__repository__",
                                        "pipelines": [
                                            {"name": "sales_order_revenue_pipeline"},
                                            {"name": "sales_customer_health_pipeline"},
                                            {"name": "supply_chain_inventory_reliability_pipeline"},
                                        ],
                                    }
                                ],
                            },
                        }
                    ],
                }
            }
        },
    )

    assert client.discover_repository("sales_order_revenue_pipeline") == ("openlakeforge-dagster", "__repository__")
    assert client.discover_repository("sales_customer_health_pipeline") == ("openlakeforge-dagster", "__repository__")
    assert client.discover_repository("supply_chain_inventory_reliability_pipeline") == (
        "openlakeforge-dagster",
        "__repository__",
    )


def test_dagster_repository_discovery_finds_job_location() -> None:
    client = e2e.DagsterClient(
        "http://dagster/graphql",
        request_json=lambda _query, _variables=None: {
            "data": {
                "workspaceOrError": {
                    "__typename": "Workspace",
                    "locationEntries": [
                        {
                            "name": "openlakeforge-dagster",
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

    assert client.discover_repository("sales_order_revenue_pipeline") == ("openlakeforge-dagster", "__repository__")


def test_dagster_repository_discovery_raises_on_workspace_error() -> None:
    client = e2e.DagsterClient(
        "http://dagster/graphql",
        request_json=lambda _query, _variables=None: {"errors": [{"message": "workspace unavailable"}]},
    )

    with pytest.raises(ServiceClientError, match="workspace unavailable"):
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
                            "name": "openlakeforge-dagster",
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

    with pytest.raises(ServiceClientError, match="user code unreachable"):
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
                                "name": "openlakeforge-dagster",
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
        "repositoryLocationName": "openlakeforge-dagster",
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
                            "name": "openlakeforge-dagster",
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
                    "name": "openlakeforge-dagster",
                }
            }
        },
        {
            "data": {
                "workspaceOrError": {
                    "__typename": "Workspace",
                    "locationEntries": [
                        {
                            "name": "openlakeforge-dagster",
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

    client = e2e.DagsterClient(
        "http://dagster/graphql",
        expected_location_names=["openlakeforge-dagster"],
        request_json=request_json,
    )

    assert client.wait_for_repository("sales_order_revenue_pipeline", timeout_seconds=1, delay=0) == (
        "openlakeforge-dagster",
        "__repository__",
    )


def test_expected_repository_location_names_reads_terraform_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        e2e,
        "terraform_output_json",
        lambda _dir, name: ["openlakeforge-dagster"] if name == "dagster_code_location_names" else None,
    )

    assert e2e.expected_repository_location_names(cfg(tmp_path)) == ["openlakeforge-dagster"]


def test_expected_repository_location_names_accepts_split_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    location_names = ["domain-a", "domain-b"]
    monkeypatch.setattr(e2e, "terraform_output_json", lambda _dir, _name: location_names)

    assert e2e.expected_repository_location_names(cfg(tmp_path)) == location_names


@pytest.mark.parametrize(
    "location_names",
    [[], ["openlakeforge-dagster", 1], ["location-a", "location-a"], "openlakeforge-dagster"],
)
def test_expected_repository_location_names_rejects_invalid_terraform_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, location_names: object
) -> None:
    monkeypatch.setattr(e2e, "terraform_output_json", lambda _dir, _name: location_names)

    with pytest.raises(e2e.E2EError, match="non-empty list"):
        e2e.expected_repository_location_names(cfg(tmp_path))


def test_terraform_output_json_reads_location_list(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(
        e2e,
        "_run",
        lambda args, *, capture=False: commands.append(args) or '["openlakeforge-dagster"]',
    )

    assert e2e.terraform_output_json(tmp_path / "contract", "dagster_code_location_names") == [
        "openlakeforge-dagster"
    ]
    assert commands == [
        [
            "terraform",
            f"-chdir={tmp_path / 'contract'}",
            "output",
            "-json",
            "dagster_code_location_names",
        ]
    ]


def test_terraform_output_json_rejects_invalid_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(e2e, "_run", lambda *_args, **_kwargs: "not-json")

    with pytest.raises(e2e.E2EError, match="not valid JSON"):
        e2e.terraform_output_json(tmp_path / "contract", "dagster_code_location_names")


def test_expected_user_code_pods_filters_to_configured_locations(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        e2e,
        "kubectl",
        lambda *_args, **_kwargs: json.dumps(
            {
                "items": [
                    {
                        "metadata": {
                            "name": "dagster-dagster-user-deployments-openlakeforge-dagster-abc",
                            "labels": {
                                "app.kubernetes.io/name": "dagster-user-deployments",
                                "app.kubernetes.io/instance": "dagster",
                                "deployment": "openlakeforge-dagster",
                            },
                        }
                    },
                    {
                        "metadata": {
                            "name": "dagster-dagster-user-deployments-sales-dagster-def",
                            "labels": {
                                "app.kubernetes.io/name": "dagster-user-deployments",
                                "app.kubernetes.io/instance": "dagster",
                                "deployment": "sales-dagster",
                            },
                        }
                    },
                    {
                        "metadata": {
                            "name": "dagster-dagster-user-deployments-supply-chain-dagster-ghi",
                            "labels": {
                                "app.kubernetes.io/name": "dagster-user-deployments",
                                "app.kubernetes.io/instance": "dagster",
                                "deployment": "supply-chain-dagster",
                            },
                        }
                    },
                    {
                        "metadata": {
                            "name": "other-release-user-code",
                            "labels": {
                                "app.kubernetes.io/name": "dagster-user-deployments",
                                "app.kubernetes.io/instance": "other-dagster",
                                "deployment": "openlakeforge-dagster",
                            },
                        }
                    },
                    {"metadata": {"name": "unrelated"}},
                ]
            }
        ),
    )

    assert e2e.expected_user_code_pods(cfg(tmp_path), ["openlakeforge-dagster"]) == [
        "dagster-dagster-user-deployments-openlakeforge-dagster-abc"
    ]
    assert e2e.expected_user_code_pods(cfg(tmp_path), ["sales-dagster", "supply-chain-dagster"]) == [
        "dagster-dagster-user-deployments-sales-dagster-def",
        "dagster-dagster-user-deployments-supply-chain-dagster-ghi",
    ]


def test_dagster_client_reloads_every_configured_location() -> None:
    reloaded: list[str] = []

    def request_json(_query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        assert variables is not None
        reloaded.append(variables["repositoryLocationName"])
        return {"data": {"reloadRepositoryLocation": {"__typename": "WorkspaceLocationEntry"}}}

    client = e2e.DagsterClient(
        "http://dagster/graphql",
        expected_location_names=["sales-dagster", "supply-chain-dagster"],
        request_json=request_json,
    )

    client.try_reload_repository_locations()

    assert reloaded == ["sales-dagster", "supply-chain-dagster"]


def test_dagster_poll_reports_failure() -> None:
    client = e2e.DagsterClient(
        "http://dagster/graphql",
        request_json=lambda _query, _variables=None: {
            "data": {"runOrError": {"__typename": "Run", "status": "FAILURE"}}
        },
    )

    with pytest.raises(ServiceClientError, match="ended with FAILURE"):
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


def test_dagster_launch_retries_transient_failure_and_keeps_launch_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    responses: list[Exception | dict[str, Any]] = [
        e2e.DagsterTransientError("HTTP 503"),
        {"data": {"launchRun": {"__typename": "LaunchRunSuccess", "run": {"runId": "run-1"}}}},
    ]
    calls: list[dict[str, Any]] = []

    def request_json(query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        if "query Workspace" in query:
            return {
                "data": {
                    "workspaceOrError": {
                        "__typename": "Workspace",
                        "locationEntries": [
                            {
                                "name": "openlakeforge-dagster",
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
            }
        if "ExistingRun" in query:
            return {"data": {"runsOrError": {"__typename": "Runs", "results": []}}}
        calls.append(variables or {})
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(dagster_client_module, "LAUNCH_RETRY_DELAY_SECONDS", 0)
    run_id = e2e.DagsterClient("http://dagster/graphql", request_json=request_json).launch(
        "sales_order_revenue_pipeline"
    )
    assert run_id == "run-1"
    assert calls[0]["executionParams"]["executionMetadata"] == calls[1]["executionParams"]["executionMetadata"]
    assert calls[0]["executionParams"]["executionMetadata"]["tags"][0]["key"] == "openlakeforge/e2e-key"
    assert calls[0]["executionParams"].get("tags") is None


def test_dagster_poll_times_out_quickly_for_non_terminal_runs() -> None:
    client = e2e.DagsterClient(
        "http://dagster/graphql",
        request_json=lambda _query, _variables=None: {
            "data": {"runOrError": {"__typename": "Run", "status": "STARTED"}}
        },
    )

    with pytest.raises(ServiceClientError, match="did not finish within 1800 seconds"):
        client.poll("sales_order_revenue_pipeline", "run-1", attempts=1, delay=0)


def test_launch_and_poll_dagster_jobs_defaults_to_previous_shell_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    timeouts: list[int] = []
    received_location_names: list[str] = []

    class Client:
        def __init__(self, _url: str, *, expected_location_names: list[str]) -> None:
            received_location_names.extend(expected_location_names)

        def launch(self, _job: str) -> str:
            return "run-1"

        def poll(self, _job: str, _run_id: str, *, timeout_seconds: int) -> None:
            timeouts.append(timeout_seconds)

    monkeypatch.delenv("DAGSTER_JOB_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setattr(e2e, "DagsterClient", Client)
    monkeypatch.setattr(e2e, "expected_repository_location_names", lambda _cfg: ["openlakeforge-dagster"])
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
        inventory=INVENTORY,
        dagster_local_port=13000,
    )

    e2e.launch_and_poll_dagster_jobs(local_cfg)

    assert timeouts == [e2e.DAGSTER_JOB_TIMEOUT_SECONDS] * len(INVENTORY.job_names)
    assert received_location_names == ["openlakeforge-dagster"]


EXPECTED_GLUE_SCHEMAS = INVENTORY.silver_namespace_names | INVENTORY.gold_namespace_names


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
    """The contract no longer publishes a namespace/database list (ADR 0022) --
    every expected database name comes straight from the inventory, and the
    check asserts against AWS directly rather than against the contract."""
    provider_contracts = {"artifact_bucket": {"bucket_name": "openlakeforge-ops"}}
    commands: list[list[str]] = []
    monkeypatch.setattr(e2e, "load_provider_contracts_or_raise", lambda _cfg: provider_contracts)
    monkeypatch.setattr(e2e, "terraform_output", lambda _dir, name: "eu-central-1" if name == "aws_region" else "")
    monkeypatch.setattr(e2e, "_run", lambda args, capture=False: commands.append(args) or "")

    e2e.check_aws_storage_and_glue(cfg(tmp_path, env="aws", suite="smoke"))

    assert ["aws", "s3api", "head-bucket", "--bucket", "openlakeforge-ops"] in commands
    glue_commands = [command for command in commands if command[:3] == ["aws", "glue", "get-database"]]
    assert len(glue_commands) == len(EXPECTED_GLUE_SCHEMAS)
    assert all(command[4] == "eu-central-1" for command in glue_commands)


def test_aws_stack_region_prefers_foundation_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setattr(e2e, "terraform_output", lambda _dir, name: "eu-central-1" if name == "aws_region" else "")

    assert e2e.aws_stack_region(cfg(tmp_path, env="aws", suite="smoke")) == "eu-central-1"


def test_check_ops_artifacts_uses_configured_bucket_for_local(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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
        inventory=INVENTORY,
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
        lambda _client, bucket, namespace, _inventory, deployed_revision: artifact_checks.append(
            (bucket, namespace, deployed_revision)
        ),
    )
    monkeypatch.setattr(e2e, "deployed_floe_manifest_revision", lambda _cfg: "sha256:" + "a" * 64)

    e2e.check_ops_artifacts(local_cfg)

    assert bucket_waits == [("custom-ops-bucket", "http://127.0.0.1:19000")]
    assert artifact_checks == [("custom-ops-bucket", "lakehouse", "sha256:" + "a" * 64)]


def test_deployed_floe_manifest_revision_reads_running_user_code_pods(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    deployed_revision = "sha256:" + "a" * 64
    local_cfg = cfg(tmp_path)
    monkeypatch.setattr(e2e, "expected_repository_location_names", lambda _cfg: ["sales", "supply-chain"])
    monkeypatch.setattr(
        e2e,
        "expected_user_code_pods",
        lambda _cfg, _locations: [
            "dagster-dagster-user-deployments-sales-abc",
            "dagster-dagster-user-deployments-supply-chain-def",
        ],
    )
    commands: list[list[str]] = []
    monkeypatch.setattr(
        e2e,
        "kubectl",
        lambda _cfg, args, *, capture=False: commands.append(args) or deployed_revision + "\n",
    )

    assert e2e.deployed_floe_manifest_revision(local_cfg) == deployed_revision
    assert commands == [
        [
            "exec",
            "-n",
            "lakehouse",
            "dagster-dagster-user-deployments-sales-abc",
            "--",
            "printenv",
            "OPENLAKEFORGE_FLOE_MANIFEST_REVISION_BUILT",
        ],
        [
            "exec",
            "-n",
            "lakehouse",
            "dagster-dagster-user-deployments-supply-chain-def",
            "--",
            "printenv",
            "OPENLAKEFORGE_FLOE_MANIFEST_REVISION_BUILT",
        ],
    ]


def test_deployed_floe_manifest_revision_rejects_inconsistent_user_code_pods(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    values = iter(("sha256:" + "a" * 64, "sha256:" + "b" * 64))
    monkeypatch.setattr(e2e, "expected_repository_location_names", lambda _cfg: ["sales", "supply-chain"])
    monkeypatch.setattr(e2e, "expected_user_code_pods", lambda _cfg, _locations: ["sales", "supply-chain"])
    monkeypatch.setattr(e2e, "kubectl", lambda *_args, **_kwargs: next(values))

    with pytest.raises(e2e.E2EError, match="disagree on the built Floe manifest revision"):
        e2e.deployed_floe_manifest_revision(cfg(tmp_path))


def test_assert_immutable_floe_manifests_verifies_deployed_revision(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    deployed_revision = "sha256:" + "a" * 64

    def verify(_client: Any, bucket: str, revision: str) -> SimpleNamespace:
        assert bucket == "ops"
        assert revision == deployed_revision
        return SimpleNamespace(entries={key: "a" * 64 for key in INVENTORY.manifest_keys})

    monkeypatch.setattr(
        e2e.revision,
        "verify",
        verify,
    )

    e2e.assert_immutable_floe_manifests(object(), "ops", INVENTORY, deployed_revision)


def test_assert_immutable_floe_manifests_requires_all_descriptor_manifests(monkeypatch: pytest.MonkeyPatch) -> None:
    deployed_revision = "sha256:" + "a" * 64
    monkeypatch.setattr(
        e2e.revision,
        "verify",
        lambda *_args: SimpleNamespace(entries={INVENTORY.manifest_keys[0]: "a" * 64}),
    )

    with pytest.raises(e2e.E2EError, match="every descriptor-discovered product manifest"):
        e2e.assert_immutable_floe_manifests(object(), "ops", INVENTORY, deployed_revision)


def test_assert_ops_artifacts_uses_legacy_manifests_for_supplied_local_image(monkeypatch: pytest.MonkeyPatch) -> None:
    checked: list[tuple[str, str]] = []

    class FakeS3Client:
        def head_object(self, *, Bucket: str, Key: str) -> None:
            checked.append((Bucket, Key))

    monkeypatch.setattr(e2e, "require_s3_prefix", lambda *_args: None)
    monkeypatch.setattr(e2e, "assert_immutable_floe_manifests", lambda *_args: pytest.fail("must use legacy checks"))

    e2e.assert_ops_artifacts(FakeS3Client(), "ops", "lakehouse", INVENTORY, "manual")

    assert checked == [("ops", key) for key in INVENTORY.manifest_keys]


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


def test_run_retry_transient_kubectl_retries_tls_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0

    def run(args: list[str], *, capture: bool = False) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise e2e.E2EError("Unable to connect to the server: net/http: TLS handshake timeout")
        return "6"

    monkeypatch.setattr(e2e, "_run", run)
    monkeypatch.setattr(e2e.time, "sleep", lambda _delay: None)

    assert e2e._run_retry_transient_kubectl(["kubectl", "exec"], attempts=3) == "6"
    assert attempts == 2


def test_run_retry_transient_kubectl_does_not_retry_query_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def run(args: list[str], *, capture: bool = False) -> str:
        nonlocal attempts
        attempts += 1
        raise e2e.E2EError("Trino query failed: TABLE_NOT_FOUND")

    monkeypatch.setattr(e2e, "_run", run)

    with pytest.raises(e2e.E2EError, match="TABLE_NOT_FOUND"):
        e2e._run_retry_transient_kubectl(["kubectl", "exec"], attempts=3)
    assert attempts == 1


def _write_dashboard_fixture(repo_root: Path, report_source_dir: str, file_name: str, *, slug: str, title: str) -> None:
    dashboards_dir = repo_root / report_source_dir / "dashboards"
    dashboards_dir.mkdir(parents=True, exist_ok=True)
    (dashboards_dir / file_name).write_text(f"dashboard_title: {title}\nslug: {slug}\n", encoding="utf-8")


def test_two_product_fixture_repo_drives_exactly_its_own_jobs_dashboards_and_marts(tmp_path: Path) -> None:
    """A descriptor change alone must move e2e's discovered work, per issue #39."""
    _write_two_product_fixture(tmp_path)
    inventory = load_domain_inventory(tmp_path)
    fixture_cfg = e2e.E2EConfig(
        env="local",
        suite="full",
        namespace="lakehouse",
        kube_context="kind-openlakeforge-local",
        repo_root=tmp_path,
        foundation_terraform_dir=tmp_path / "foundation",
        contract_terraform_dir=tmp_path / "contract",
        inventory=inventory,
    )

    assert fixture_cfg.inventory.job_names == ("widgets_alpha_pipeline", "widgets_beta_pipeline")
    assert fixture_cfg.inventory.gold_mart_names == (
        "widgets_alpha_gold.mart_alpha_summary",
        "widgets_beta_gold.mart_beta_summary",
    )

    # Dashboard identity comes from the exported Superset YAML, not an
    # asset_prefix/displayName convention — a title/slug that doesn't match
    # that convention, and a product with two dashboards, must both work.
    _write_dashboard_fixture(
        tmp_path,
        inventory.default_product.report_source_dir,
        "Overview_1.yaml",
        slug="widgets-alpha-overview",
        title="Widgets Alpha Overview Board",
    )
    beta_product = next(product for product in inventory.products if product.id == "beta")
    _write_dashboard_fixture(
        tmp_path, beta_product.report_source_dir, "Beta_Main_1.yaml", slug="widgets-beta-main", title="Widgets Beta"
    )
    _write_dashboard_fixture(
        tmp_path, beta_product.report_source_dir, "Beta_Detail_1.yaml", slug="widgets-beta-detail", title="Beta Detail"
    )

    expected = e2e.discovered_dashboards(fixture_cfg)

    assert expected == {
        "widgets-alpha-overview": "Widgets Alpha Overview Board",
        "widgets-beta-main": "Widgets Beta",
        "widgets-beta-detail": "Beta Detail",
    }
    e2e.assert_superset_dashboards(
        [
            {"slug": "widgets-alpha-overview", "dashboard_title": "Widgets Alpha Overview Board"},
            {"slug": "widgets-beta-main", "dashboard_title": "Widgets Beta"},
            {"slug": "widgets-beta-detail", "dashboard_title": "Beta Detail"},
        ],
        expected,
    )
    # Pipeline Job attempts are assessed by the launch-and-poll assertion, not
    # by the initial platform readiness check. Historical failed attempts must
    # not prevent a later suite from starting.
    assert e2e.classify_pod_health(
        {
            "items": [
                {
                    "metadata": {
                        "name": "widgets-alpha-run-pod",
                        "ownerReferences": [{"kind": "Job", "name": "widgets_alpha_pipeline-run"}],
                    },
                    "status": {"phase": "Failed"},
                }
            ]
        }
    ) == []


def test_discovered_dashboards_rejects_a_product_with_no_dashboard_export(tmp_path: Path) -> None:
    """A missing dashboard export must fail loudly, not pass by contributing nothing."""
    _write_two_product_fixture(tmp_path)
    inventory = load_domain_inventory(tmp_path)
    fixture_cfg = e2e.E2EConfig(
        env="local",
        suite="full",
        namespace="lakehouse",
        kube_context="kind-openlakeforge-local",
        repo_root=tmp_path,
        foundation_terraform_dir=tmp_path / "foundation",
        contract_terraform_dir=tmp_path / "contract",
        inventory=inventory,
    )
    # Only the default (first) product exports a dashboard; the second has none.
    _write_dashboard_fixture(
        tmp_path,
        inventory.default_product.report_source_dir,
        "Overview_1.yaml",
        slug="widgets-alpha-overview",
        title="Widgets Alpha Overview Board",
    )

    with pytest.raises(e2e.E2EError, match="exports no Superset dashboards"):
        e2e.discovered_dashboards(fixture_cfg)


def test_discovered_dashboards_accepts_yml_suffixed_exports(tmp_path: Path) -> None:
    """superset.build_report_bundle packages both .yaml and .yml — discovery must match."""
    _write_two_product_fixture(tmp_path)
    inventory = load_domain_inventory(tmp_path)
    fixture_cfg = e2e.E2EConfig(
        env="local",
        suite="full",
        namespace="lakehouse",
        kube_context="kind-openlakeforge-local",
        repo_root=tmp_path,
        foundation_terraform_dir=tmp_path / "foundation",
        contract_terraform_dir=tmp_path / "contract",
        inventory=inventory,
    )
    beta_product = next(product for product in inventory.products if product.id == "beta")
    _write_dashboard_fixture(
        tmp_path,
        inventory.default_product.report_source_dir,
        "Overview_1.yaml",
        slug="widgets-alpha-overview",
        title="Widgets Alpha Overview Board",
    )
    _write_dashboard_fixture(
        tmp_path, beta_product.report_source_dir, "Beta_Main_1.yml", slug="widgets-beta-main", title="Widgets Beta"
    )

    assert e2e.discovered_dashboards(fixture_cfg) == {
        "widgets-alpha-overview": "Widgets Alpha Overview Board",
        "widgets-beta-main": "Widgets Beta",
    }
