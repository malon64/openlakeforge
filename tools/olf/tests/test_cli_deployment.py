from __future__ import annotations

import pytest
from typer.testing import CliRunner

from olf.cli import app

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


def test_unknown_profile_is_rejected_before_building_an_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "olf.commands.deployment._build_engine",
        lambda *a, **k: pytest.fail("engine should not be built for an invalid profile"),
    )

    result = runner.invoke(app, ["deploy", "--profile", "bogus"])

    assert result.exit_code == 1
