from __future__ import annotations

import pytest

from olf.deployment.context import DeploymentContext, Provider
from olf.deployment.engine import DeploymentEngine, DeploymentPhase, build_provider
from olf.deployment.errors import UnsupportedProviderError


class _FakeProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def foundation_up(self) -> None:
        self.calls.append("foundation_up")

    def foundation_down(self, *, force: bool = False) -> None:
        self.calls.append(f"foundation_down(force={force})")

    def prepare_images(self) -> None:
        self.calls.append("prepare_images")

    def platform_up(self) -> None:
        self.calls.append("platform_up")

    def platform_down(self) -> None:
        self.calls.append("platform_down")

    def artifacts_deploy(self) -> None:
        self.calls.append("artifacts_deploy")

    def status(self) -> str:
        self.calls.append("status")
        return "report"

    def forward(self) -> None:
        self.calls.append("forward")


def test_deploy_all_runs_phases_in_adr_0008_order() -> None:
    provider = _FakeProvider()
    engine = DeploymentEngine(provider)

    engine.deploy()

    assert provider.calls == ["foundation_up", "prepare_images", "platform_up", "artifacts_deploy"]


@pytest.mark.parametrize(
    ("phase", "expected"),
    [
        (DeploymentPhase.FOUNDATION, ["foundation_up"]),
        (DeploymentPhase.PREFETCH, ["prepare_images"]),
        (DeploymentPhase.PLATFORM, ["platform_up"]),
        (DeploymentPhase.ARTIFACTS, ["artifacts_deploy"]),
    ],
)
def test_deploy_single_phase_runs_only_that_step(phase: DeploymentPhase, expected: list[str]) -> None:
    provider = _FakeProvider()
    engine = DeploymentEngine(provider)

    engine.deploy(phase)

    assert provider.calls == expected


def test_destroy_all_tears_down_platform_before_foundation() -> None:
    provider = _FakeProvider()
    engine = DeploymentEngine(provider)

    engine.destroy(force=True)

    assert provider.calls == ["platform_down", "foundation_down(force=True)"]


def test_destroy_platform_phase_leaves_foundation_running() -> None:
    provider = _FakeProvider()
    engine = DeploymentEngine(provider)

    engine.destroy(DeploymentPhase.PLATFORM)

    assert provider.calls == ["platform_down"]


def test_destroy_foundation_phase_only() -> None:
    provider = _FakeProvider()
    engine = DeploymentEngine(provider)

    engine.destroy(DeploymentPhase.FOUNDATION)

    assert provider.calls == ["foundation_down(force=False)"]


def test_status_and_forward_delegate_to_provider() -> None:
    provider = _FakeProvider()
    engine = DeploymentEngine(provider)

    assert engine.status() == "report"
    engine.forward()

    assert provider.calls == ["status", "forward"]


def test_build_provider_rejects_unsupported_providers(tmp_path) -> None:  # noqa: ANN001
    context = DeploymentContext.for_provider(Provider.AWS, repo_root=tmp_path)

    with pytest.raises(UnsupportedProviderError):
        build_provider(context)


def test_build_provider_returns_local_provider(tmp_path) -> None:  # noqa: ANN001
    context = DeploymentContext.local(repo_root=tmp_path)

    provider = build_provider(context, environ={})

    assert provider.context is context
