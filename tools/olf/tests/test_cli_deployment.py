from __future__ import annotations

from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from olf.cli import app
from olf.profile import StageName

runner = CliRunner()


class _FakeEngine:
    def __init__(self) -> None:
        self.deploy_calls: list = []
        self.destroy_calls: list = []
        self.forward_called = False
        self.raise_on_deploy: Exception | None = None

    def deploy(self, phase):  # noqa: ANN001
        self.deploy_calls.append(phase)
        if self.raise_on_deploy is not None:
            raise self.raise_on_deploy

    def destroy(self, phase, *, force=False):  # noqa: ANN001
        self.destroy_calls.append((phase, force))

    def status(self):  # noqa: ANN202
        from olf.deployment.status import StatusReport, StatusSection

        return StatusReport(sections=(StatusSection(title="Pods", output="no resources"),))

    def forward(self) -> None:
        self.forward_called = True


@pytest.fixture
def fake_engine(monkeypatch: pytest.MonkeyPatch) -> _FakeEngine:
    engine = _FakeEngine()
    monkeypatch.setattr("olf.commands.deployment._build_context", lambda *a, **k: object())
    monkeypatch.setattr("olf.commands.deployment._build_engine", lambda *a, **k: engine)
    return engine


def test_deploy_forwards_kubeconfig_path_to_build_context(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def _capture_build_context(*args, **kwargs):  # noqa: ANN002, ANN003
        calls.append(kwargs)
        return object()

    engine = _FakeEngine()
    monkeypatch.setattr("olf.commands.deployment._build_context", _capture_build_context)
    monkeypatch.setattr("olf.commands.deployment._build_engine", lambda *a, **k: engine)

    result = runner.invoke(app, ["deploy", "--kubeconfig-path", "/tmp/custom/kind-smoke.yaml"])

    assert result.exit_code == 0
    assert calls[0]["kubeconfig_path"] == "/tmp/custom/kind-smoke.yaml"


def test_build_context_honors_kubeconfig_path_override_for_cloud_providers(tmp_path: Path) -> None:
    """Regression test: `_build_context` used to gate `kubeconfig_path` on
    `Provider.LOCAL`, silently ignoring the documented `AWS_KUBECONFIG_PATH`/
    `AZURE_KUBECONFIG_PATH` concurrent-deployment overrides for cloud.
    """
    import os

    from olf.commands.deployment import _build_context

    override = tmp_path / "custom" / "aws-smoke.yaml"
    old_repo_root = os.environ.get("OPENLAKEFORGE_REPO_ROOT")
    os.environ["OPENLAKEFORGE_REPO_ROOT"] = str(tmp_path)
    try:
        context = _build_context(
            "aws", profile="full", namespace="", cluster_name="", kubeconfig_path=str(override)
        )
    finally:
        if old_repo_root is None:
            os.environ.pop("OPENLAKEFORGE_REPO_ROOT", None)
        else:
            os.environ["OPENLAKEFORGE_REPO_ROOT"] = old_repo_root

    assert context.paths.kubeconfig_path == override


def test_deploy_defaults_to_all_phases(fake_engine: _FakeEngine) -> None:
    from olf.deployment.engine import DeploymentPhase

    result = runner.invoke(app, ["deploy", "--provider", "local", "--profile", "full"])

    assert result.exit_code == 0
    assert fake_engine.deploy_calls == [DeploymentPhase.ALL]


def test_deploy_routes_phase_flag(fake_engine: _FakeEngine) -> None:
    from olf.deployment.engine import DeploymentPhase

    result = runner.invoke(app, ["deploy", "--phase", "platform"])

    assert result.exit_code == 0
    assert fake_engine.deploy_calls == [DeploymentPhase.PLATFORM]


def test_deploy_rejects_unknown_phase(fake_engine: _FakeEngine) -> None:
    result = runner.invoke(app, ["deploy", "--phase", "bogus"])

    assert result.exit_code == 1
    assert fake_engine.deploy_calls == []


def test_deploy_rejects_unknown_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    result = runner.invoke(app, ["deploy", "--provider", "bogus"])

    assert result.exit_code == 1


def test_deploy_maps_deployment_error_to_exit_1(fake_engine: _FakeEngine) -> None:
    from olf.deployment.errors import DeploymentPreconditionError

    fake_engine.raise_on_deploy = DeploymentPreconditionError("kubeconfig missing")

    result = runner.invoke(app, ["deploy"])

    assert result.exit_code == 1
    assert "kubeconfig missing" in result.output


def test_destroy_threads_force_flag(fake_engine: _FakeEngine) -> None:
    from olf.deployment.engine import DeploymentPhase

    result = runner.invoke(app, ["destroy", "--phase", "foundation", "--force"])

    assert result.exit_code == 0
    assert fake_engine.destroy_calls == [(DeploymentPhase.FOUNDATION, True)]


def test_destroy_rejects_artifacts_phase(fake_engine: _FakeEngine) -> None:
    result = runner.invoke(app, ["destroy", "--phase", "artifacts"])

    assert result.exit_code == 1
    assert fake_engine.destroy_calls == []


def test_status_prints_rendered_report(fake_engine: _FakeEngine) -> None:
    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "=== Pods ===" in result.output


def test_forward_delegates_to_engine(fake_engine: _FakeEngine) -> None:
    result = runner.invoke(app, ["forward"])

    assert result.exit_code == 0
    assert fake_engine.forward_called is True


def test_profile_full_and_slim_are_accepted(fake_engine: _FakeEngine) -> None:
    assert runner.invoke(app, ["deploy", "--profile", "full"]).exit_code == 0
    assert runner.invoke(app, ["deploy", "--profile", "slim"]).exit_code == 0


def test_deploy_accepts_aws_and_azure_providers(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # noqa: ANN001
    """Exercises the real `_build_context` (no subprocess calls - pure data) for both cloud
    providers, only mocking `_build_engine` to avoid touching real Terraform/AWS/Azure CLIs.
    """
    engine = _FakeEngine()
    monkeypatch.setattr("olf.commands.deployment._build_engine", lambda *a, **k: engine)
    monkeypatch.setenv("OPENLAKEFORGE_REPO_ROOT", str(tmp_path))

    for provider in ("aws", "azure"):
        result = runner.invoke(app, ["deploy", "--provider", provider, "--phase", "foundation"])
        assert result.exit_code == 0, result.output


def test_unknown_profile_is_rejected_before_building_an_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "olf.commands.deployment._build_engine",
        lambda *a, **k: pytest.fail("engine should not be built for an invalid profile"),
    )

    result = runner.invoke(app, ["deploy", "--profile", "bogus"])

    assert result.exit_code == 1


def test_an_invalid_toolchain_mode_surfaces_as_a_clean_cli_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A misspelled OLF_TOOLCHAIN_MODE is a user configuration mistake, not
    a provisioning failure - but Toolkit.default() (built inside every
    lifecycle command's _build_engine) raises it while resolving the
    executable strategy, well before any provider-specific setup runs. It
    must surface the same clean way every other DeploymentError does, not
    as a raw traceback."""
    from olf.deployment.errors import ToolchainError

    monkeypatch.setenv("OLF_TOOLCHAIN_MODE", "bogus")

    result = runner.invoke(app, ["doctor", "--provider", "local"])

    assert result.exit_code != 0
    assert not isinstance(result.exception, ToolchainError)
    assert "OLF_TOOLCHAIN_MODE" in result.output


def _write_profile(root: Path, *, provider: str = "local", stages: str = "    dev:\n      enabled: true\n") -> None:
    (root / "openlakeforge.yaml").write_text(
        "apiVersion: openlakeforge.io/v1alpha1\n"
        "kind: DeploymentProfile\n"
        "metadata:\n"
        "  name: acme-data\n"
        "spec:\n"
        f"  provider:\n    type: {provider}\n"
        "  preset: slim\n"
        "  stages:\n" + stages
    )


def test_the_deployment_profile_decides_the_stages(tmp_path: Path) -> None:
    from olf.commands._shared import resolve_topology
    from olf.deployment.context import Provider

    _write_profile(tmp_path, stages="    dev:\n      enabled: true\n    prod:\n      enabled: true\n")

    topology = resolve_topology(tmp_path, provider=Provider.LOCAL)

    assert [stage.name.value for stage in topology.stages if stage.enabled] == ["dev", "prod"]


def test_an_explicit_preset_selects_the_deprecated_single_dev_shorthand(tmp_path: Path) -> None:
    from olf.commands._shared import resolve_topology
    from olf.deployment.context import Provider

    _write_profile(tmp_path, stages="    dev:\n      enabled: true\n    prod:\n      enabled: true\n")

    topology = resolve_topology(tmp_path, provider=Provider.LOCAL, preset="full")

    assert [stage.name.value for stage in topology.stages if stage.enabled] == ["dev"]
    assert topology.stage(StageName.DEV).capabilities.analytics is True


def test_a_profile_for_another_provider_does_not_describe_this_deployment(tmp_path: Path) -> None:
    from olf.commands._shared import resolve_topology
    from olf.deployment.context import Provider

    _write_profile(tmp_path, provider="aws", stages="    dev:\n      enabled: true\n    prod:\n      enabled: true\n")

    topology = resolve_topology(tmp_path, provider=Provider.LOCAL)

    assert topology.provider == Provider.LOCAL
    assert [stage.name.value for stage in topology.stages if stage.enabled] == ["dev"]


def test_an_invalid_profile_fails_closed(tmp_path: Path) -> None:
    from olf.commands._shared import resolve_topology
    from olf.deployment.context import Provider

    (tmp_path / "openlakeforge.yaml").write_text("apiVersion: openlakeforge.io/v1alpha1\nkind: NotAProfile\n")

    with pytest.raises(typer.Exit):
        resolve_topology(tmp_path, provider=Provider.LOCAL)


def test_local_rejects_a_namespace_override(tmp_path: Path) -> None:
    """Terraform derives the local namespaces from the topology, so an
    override would only move what the later phases look for."""
    _write_profile(tmp_path)

    result = runner.invoke(
        app,
        ["deploy", "--provider", "local", "--namespace", "custom", "--project-root", str(tmp_path)],
    )

    assert result.exit_code == 1
    assert "--namespace is not supported for the local provider" in result.output
