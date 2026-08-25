import os
from pathlib import Path

import pytest
from conftest import E2E_INVENTORY, E2E_REPO_ROOT, e2e_cfg

from olf.e2e import _runner, _shell
from olf.e2e._shell import E2EError, Environment


@pytest.mark.parametrize("env", ["local", "azure", "aws"])
def test_default_suite_is_full(env: Environment) -> None:
    assert _runner.default_suite(env) == "full"


def test_aws_default_suite_includes_smoke_and_full_checks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[str] = []

    monkeypatch.setattr(_runner, "check_commands", lambda _cfg: None)
    monkeypatch.setattr(_runner, "prepare_kube_context", lambda _cfg: None)
    monkeypatch.setattr(_runner, "check_pods_ready", lambda _cfg: None)
    monkeypatch.setattr(_runner, "run_smoke", lambda _cfg: calls.append("smoke"))
    monkeypatch.setattr(_runner, "run_full", lambda _cfg: calls.append("full"))

    _runner.run("aws", repo_root=E2E_REPO_ROOT)

    assert calls == ["smoke", "full"]


def test_aws_explicit_smoke_suite_skips_full_checks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[str] = []

    monkeypatch.setattr(_runner, "check_commands", lambda _cfg: None)
    monkeypatch.setattr(_runner, "prepare_kube_context", lambda _cfg: None)
    monkeypatch.setattr(_runner, "check_pods_ready", lambda _cfg: None)
    monkeypatch.setattr(_runner, "run_smoke", lambda _cfg: calls.append("smoke"))
    monkeypatch.setattr(_runner, "run_full", lambda _cfg: calls.append("full"))

    _runner.run("aws", suite="smoke", repo_root=E2E_REPO_ROOT)

    assert calls == ["smoke"]


def test_local_smoke_runs_only_the_descriptor_default_product(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[object] = []
    local_cfg = e2e_cfg(tmp_path, suite="smoke")

    monkeypatch.setattr(_runner, "check_trino_catalog", lambda _cfg: calls.append("catalog"))
    monkeypatch.setattr(_runner, "check_catalog_namespaces", lambda _cfg: calls.append("namespaces"))
    monkeypatch.setattr(
        _runner,
        "launch_and_poll_dagster_jobs",
        lambda _cfg, *, products=None: calls.append(tuple(product.id for product in products or ())),
    )
    monkeypatch.setattr(
        _runner,
        "check_trino_product_tables_and_marts",
        lambda _cfg, product: calls.append(("tables", product.id)),
    )

    _runner.run_smoke(local_cfg)

    assert calls == [
        "catalog",
        "namespaces",
        (E2E_INVENTORY.default_product.id,),
        ("tables", E2E_INVENTORY.default_product.id),
    ]


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ("local", ["namespaces", "jobs", "tables", "recovery", "superset", "openmetadata", "artifacts"]),
        ("azure", ["namespaces", "jobs", "tables", "superset", "openmetadata", "artifacts"]),
        ("aws", ["namespaces", "jobs", "tables", "superset", "openmetadata", "artifacts"]),
    ],
)
def test_run_full_only_restarts_polaris_for_local(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, env: Environment, expected: list[str]
) -> None:
    calls: list[str] = []

    monkeypatch.setattr(_runner, "check_catalog_namespaces", lambda _cfg: calls.append("namespaces"))
    monkeypatch.setattr(_runner, "launch_and_poll_dagster_jobs", lambda _cfg: calls.append("jobs"))
    monkeypatch.setattr(_runner, "check_trino_tables_and_marts", lambda _cfg: calls.append("tables"))
    monkeypatch.setattr(_runner, "check_polaris_restart_recovery", lambda _cfg: calls.append("recovery"))
    monkeypatch.setattr(_runner, "check_superset_dashboards", lambda _cfg: calls.append("superset"))
    monkeypatch.setattr(_runner, "check_openmetadata_assets", lambda _cfg: calls.append("openmetadata"))
    monkeypatch.setattr(_runner, "check_ops_artifacts", lambda _cfg: calls.append("artifacts"))
    monkeypatch.setattr(_runner, "configured_layers", lambda _cfg: {"governance": True, "analytics": True})

    _runner.run_full(e2e_cfg(tmp_path, env=env))

    assert calls == expected


def test_run_full_skips_disabled_layer_assertions_and_reports_them(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []
    messages: list[str] = []

    monkeypatch.setattr(_runner, "check_catalog_namespaces", lambda _cfg: calls.append("namespaces"))
    monkeypatch.setattr(_runner, "launch_and_poll_dagster_jobs", lambda _cfg: calls.append("jobs"))
    monkeypatch.setattr(_runner, "check_trino_tables_and_marts", lambda _cfg: calls.append("tables"))
    monkeypatch.setattr(_runner, "check_polaris_restart_recovery", lambda _cfg: calls.append("recovery"))
    monkeypatch.setattr(_runner, "check_superset_dashboards", lambda _cfg: calls.append("superset"))
    monkeypatch.setattr(_runner, "check_openmetadata_assets", lambda _cfg: calls.append("openmetadata"))
    monkeypatch.setattr(_runner, "check_ops_artifacts", lambda _cfg: calls.append("artifacts"))
    monkeypatch.setattr(_runner, "configured_layers", lambda _cfg: {"governance": False, "analytics": False})
    monkeypatch.setattr(_runner.log, "info", messages.append)

    _runner.run_full(e2e_cfg(tmp_path))

    assert calls == ["namespaces", "jobs", "tables", "recovery", "artifacts"]
    assert "Skipped e2e assertions: Superset dashboards, OpenMetadata governance assets" in messages


def test_prepare_kube_context_refreshes_existing_aws_context(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    commands: list[list[str]] = []
    kubeconfig = tmp_path / "aws.yaml"
    monkeypatch.setenv("KUBECONFIG", str(kubeconfig))

    def run(args: list[str], *, capture: bool = False) -> str:
        commands.append(args)
        return ""

    # prepare_kube_context calls `_run` directly (bound in _runner); its final
    # `_run_retry(...)` call resolves `_run` from _shell's own namespace, so
    # both bindings need the same fake to capture every command.
    monkeypatch.setattr(_runner, "_run", run)
    monkeypatch.setattr(_shell, "_run", run)
    monkeypatch.setattr(_runner, "_kubectl_executable", lambda: "kubectl")
    monkeypatch.setattr(
        _runner,
        "terraform_output",
        lambda _dir, name: {
            "aws_region": "eu-west-1",
            "cluster_name": "limited-eks-openlakeforge-poc",
        }[name],
    )

    _runner.prepare_kube_context(e2e_cfg(tmp_path, env="aws", suite="smoke"))

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
    env: Environment,
    expected_command_prefix: list[str],
    terraform_outputs: dict[str, str],
) -> None:
    commands: list[list[str]] = []
    monkeypatch.delenv("KUBECONFIG", raising=False)
    monkeypatch.delenv(f"{env.upper()}_KUBECONFIG_PATH", raising=False)
    run = lambda args, capture=False: commands.append(args) or ""  # noqa: E731
    monkeypatch.setattr(_runner, "_run", run)
    monkeypatch.setattr(_shell, "_run", run)
    monkeypatch.setattr(_runner, "_kubectl_executable", lambda: "kubectl")
    monkeypatch.setattr(_runner, "terraform_output", lambda _dir, name: terraform_outputs[name])

    _runner.prepare_kube_context(e2e_cfg(tmp_path, env=env, suite="smoke"))

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

    monkeypatch.setattr(_runner, "_run", run)
    monkeypatch.setattr(_runner, "_kubectl_executable", lambda: "kubectl")

    _runner.prepare_kube_context(e2e_cfg(tmp_path))

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
            raise E2EError("context missing")
        return ""

    monkeypatch.setattr(_runner, "_run", run)
    monkeypatch.setattr(_shell, "_run", run)
    monkeypatch.setattr(_shell.time, "sleep", lambda _delay: None)
    monkeypatch.setattr(_runner, "_kubectl_executable", lambda: "kubectl")
    monkeypatch.setattr(
        _runner,
        "terraform_output",
        lambda _dir, name: {
            "aws_region": "eu-west-1",
            "cluster_name": "limited-eks-openlakeforge-poc",
        }[name],
    )

    _runner.prepare_kube_context(e2e_cfg(tmp_path, env="aws", suite="smoke"))

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


def test_terraform_output_json_reads_location_list(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(
        _shell,
        "_run",
        lambda args, *, capture=False: commands.append(args) or '["openlakeforge-dagster"]',
    )
    monkeypatch.setattr(_shell, "_terraform_executable", lambda: "terraform")

    assert _shell.terraform_output_json(tmp_path / "contract", "dagster_code_location_names") == [
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
    monkeypatch.setattr(_shell, "_run", lambda *_args, **_kwargs: "not-json")
    monkeypatch.setattr(_shell, "_terraform_executable", lambda: "terraform")

    with pytest.raises(E2EError, match="not valid JSON"):
        _shell.terraform_output_json(tmp_path / "contract", "dagster_code_location_names")


def test_run_retry_retries_transient_command_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0

    def run(args: list[str], *, capture: bool = False) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise E2EError("temporary failure")
        return "ok"

    monkeypatch.setattr(_shell, "_run", run)
    monkeypatch.setattr(_shell.time, "sleep", lambda _delay: None)

    assert _shell._run_retry(["kubectl", "cluster-info"], capture=True, attempts=2, delay=0) == "ok"
    assert attempts == 2


def test_run_retry_transient_kubectl_retries_tls_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0

    def run(args: list[str], *, capture: bool = False) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise E2EError("Unable to connect to the server: net/http: TLS handshake timeout")
        return "6"

    monkeypatch.setattr(_shell, "_run", run)
    monkeypatch.setattr(_shell.time, "sleep", lambda _delay: None)

    assert _shell._run_retry_transient_kubectl(["kubectl", "exec"], attempts=3) == "6"
    assert attempts == 2


def test_run_retry_transient_kubectl_does_not_retry_query_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def run(args: list[str], *, capture: bool = False) -> str:
        nonlocal attempts
        attempts += 1
        raise E2EError("Trino query failed: TABLE_NOT_FOUND")

    monkeypatch.setattr(_shell, "_run", run)

    with pytest.raises(E2EError, match="TABLE_NOT_FOUND"):
        _shell._run_retry_transient_kubectl(["kubectl", "exec"], attempts=3)
    assert attempts == 1
