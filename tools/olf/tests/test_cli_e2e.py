from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from olf.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_real_contract_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    """`applied_contract_environment` shells out to `terraform output`; every test here mocks
    the contracts module so no real subprocess runs.
    """
    from olf import contracts as contracts_module

    monkeypatch.setattr(contracts_module, "load_provider_contracts", lambda terraform_dir: None)
    monkeypatch.setattr(
        contracts_module, "build_contract_env", lambda base, contracts_value, *, repo_root: ({}, [])
    )


def test_e2e_run_rejects_unknown_env() -> None:
    result = runner.invoke(app, ["e2e", "run", "--env", "bogus"])

    assert result.exit_code == 1
    assert "unknown --env" in result.output


def test_e2e_run_rejects_unknown_suite() -> None:
    result = runner.invoke(app, ["e2e", "run", "--env", "local", "--suite", "bogus"])

    assert result.exit_code == 1
    assert "unknown --suite" in result.output


def test_e2e_run_is_self_sufficient_no_shell_wrapper_needed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict] = []

    def _fake_run(env, *, suite, namespace, kube_context, repo_root):  # noqa: ANN001
        calls.append(
            {"env": env, "suite": suite, "namespace": namespace, "kube_context": kube_context, "repo_root": repo_root}
        )

    monkeypatch.setenv("OPENLAKEFORGE_REPO_ROOT", str(tmp_path))
    monkeypatch.setattr("olf.e2e.run", _fake_run)

    result = runner.invoke(app, ["e2e", "run", "--env", "local"])

    assert result.exit_code == 0, result.output
    assert calls == [
        {
            "env": "local",
            "suite": None,
            "namespace": "lakehouse",
            "kube_context": "kind-openlakeforge-local",
            "repo_root": tmp_path,
        }
    ]


def test_e2e_run_falls_back_to_provider_cluster_name_when_kube_context_unset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The `aws-e2e`/`azure-e2e` Make targets never set `KUBE_CONTEXT` - only
    `AWS_CLUSTER_NAME`/`AZURE_CLUSTER_NAME`. `applied_contract_environment` always
    exports `KUBE_CONTEXT`, so it must resolve to the provider's cluster-name
    fallback rather than an empty string that would defeat `olf.e2e.run`'s own
    `os.environ.get("KUBE_CONTEXT", ...)` fallback.
    """
    calls: list[dict] = []

    def _fake_run(env, *, suite, namespace, kube_context, repo_root):  # noqa: ANN001
        calls.append({"kube_context": kube_context})

    monkeypatch.setenv("OPENLAKEFORGE_REPO_ROOT", str(tmp_path))
    monkeypatch.setenv("AWS_CLUSTER_NAME", "limited-eks-openlakeforge-poc")
    monkeypatch.delenv("KUBE_CONTEXT", raising=False)
    monkeypatch.setattr("olf.e2e.run", _fake_run)

    result = runner.invoke(app, ["e2e", "run", "--env", "aws"])

    assert result.exit_code == 0, result.output
    assert calls == [{"kube_context": "limited-eks-openlakeforge-poc"}]


def test_e2e_run_maps_e2e_error_to_exit_1(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from olf import e2e

    def _raise(*a, **k):  # noqa: ANN002, ANN003, ARG001
        raise e2e.E2EError("cluster not reachable")

    monkeypatch.setenv("OPENLAKEFORGE_REPO_ROOT", str(tmp_path))
    monkeypatch.setattr("olf.e2e.run", _raise)

    result = runner.invoke(app, ["e2e", "run", "--env", "local"])

    assert result.exit_code == 1
    assert "cluster not reachable" in result.output
