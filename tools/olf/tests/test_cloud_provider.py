"""`CloudProvider` orchestration tests.

The facts-before-env ordering here is the single highest-risk detail in the
AWS/Azure port: `DeploymentContext.kube_context` is unknown until the
foundation's Terraform outputs are read, unlike local's static
`kind-<cluster>`. `env` must resolve `_foundation_facts` before building the
command environment, or `KUBE_CONTEXT` gets baked in empty.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _cloud_support import FakeCloudBackend

from olf.deployment.cloud.backend import FoundationFacts
from olf.deployment.cloud.config import CloudDeploymentConfig
from olf.deployment.cloud.provider import CloudProvider
from olf.deployment.context import DeploymentContext
from olf.deployment.engine import DeploymentPhase, Toolkit
from olf.deployment.errors import DeploymentPreconditionError

_FACTS = FoundationFacts(
    cluster_name="eks-openlakeforge-poc",
    kube_context="eks-openlakeforge-poc",
    project_code_repository="123.dkr.ecr.eu-west-1.amazonaws.com/project-code",
    superset_repository="123.dkr.ecr.eu-west-1.amazonaws.com/superset",
    aws_region="eu-west-1",
)


def _toolkit() -> Toolkit:
    return Toolkit.default(overrides={t: Path(t) for t in ("terraform", "docker", "kind", "kubectl", "helm")})


def _config(tmp_path: Path) -> CloudDeploymentConfig:
    context = DeploymentContext.aws(repo_root=tmp_path)
    return CloudDeploymentConfig.from_environment({}, context=context)


def test_env_raises_before_foundation_exists(tmp_path: Path) -> None:
    config = _config(tmp_path)
    backend = FakeCloudBackend(scope="aws")
    provider = CloudProvider.create(config, backend, toolkit=_toolkit(), environ={})

    with pytest.raises(DeploymentPreconditionError, match="foundation Terraform state is missing"):
        _ = provider.env


def test_env_resolves_kube_context_from_foundation_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    backend = FakeCloudBackend(scope="aws", facts=_FACTS)
    provider = CloudProvider.create(config, backend, toolkit=_toolkit(), environ={})
    monkeypatch.setattr(
        "olf.deployment.cloud.foundation.require_foundation_facts", lambda cfg, tools, be, *, env: _FACTS
    )

    resolved_env = provider.env

    assert resolved_env["KUBE_CONTEXT"] == _FACTS.kube_context


def test_env_is_cached_and_facts_are_resolved_only_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    backend = FakeCloudBackend(scope="aws", facts=_FACTS)
    provider = CloudProvider.create(config, backend, toolkit=_toolkit(), environ={})
    calls: list[int] = []

    def _fake_require(cfg, tools, be, *, env):  # noqa: ANN001, ARG001
        calls.append(1)
        return _FACTS

    monkeypatch.setattr("olf.deployment.cloud.foundation.require_foundation_facts", _fake_require)

    _ = provider.env
    _ = provider.env

    assert len(calls) == 1


def test_foundation_up_does_not_require_pre_existing_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`foundation_up` must use `_base_env`, not `env` - there is no foundation yet to derive a
    kube_context from, so reading `env` here would raise before the foundation exists.
    """
    config = _config(tmp_path)
    backend = FakeCloudBackend(scope="aws", facts=_FACTS)
    provider = CloudProvider.create(config, backend, toolkit=_toolkit(), environ={})
    calls: list[str] = []

    def _fake_foundation_up(cfg, tools, be, *, environ, env):  # noqa: ANN001, ARG001
        calls.append("foundation_up")
        return _FACTS

    monkeypatch.setattr("olf.deployment.cloud.foundation.foundation_up", _fake_foundation_up)

    provider.foundation_up()

    assert calls == ["foundation_up"]


def test_platform_up_after_foundation_up_resolves_fresh_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact sequence `DeploymentEngine.deploy()` (phase=ALL) exercises on one provider
    instance: foundation_up() then platform_up(), with platform_up() needing the
    just-created foundation's facts.
    """
    config = _config(tmp_path)
    backend = FakeCloudBackend(scope="aws", facts=_FACTS)
    provider = CloudProvider.create(config, backend, toolkit=_toolkit(), environ={})

    monkeypatch.setattr(
        "olf.deployment.cloud.foundation.foundation_up",
        lambda cfg, tools, be, *, environ, env: _FACTS,  # noqa: ARG005
    )
    monkeypatch.setattr(
        "olf.deployment.cloud.foundation.require_foundation_facts",
        lambda cfg, tools, be, *, env: _FACTS,  # noqa: ARG005
    )
    seen_facts = []
    monkeypatch.setattr(
        "olf.deployment.cloud.platform.platform_up",
        lambda cfg, tools, be, facts, *, env: seen_facts.append(facts),  # noqa: ARG005
    )

    provider.foundation_up()
    provider.platform_up()

    assert seen_facts == [_FACTS]


def test_prepare_images_is_a_no_op(tmp_path: Path) -> None:
    config = _config(tmp_path)
    backend = FakeCloudBackend(scope="aws")
    provider = CloudProvider.create(config, backend, toolkit=_toolkit(), environ={})

    provider.prepare_images()  # must not raise, must not touch the foundation


def test_foundation_doctor_does_not_require_or_probe_later_phase_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    backend = FakeCloudBackend(scope="aws", facts=_FACTS)
    provider = CloudProvider.create(config, backend, toolkit=_toolkit(), environ={})
    required: list[str] = []
    monkeypatch.setattr(
        "olf.deployment.cloud.provider.base_report",
        lambda **kwargs: required.extend(kwargs["required_tools"]) or [],
    )
    monkeypatch.setattr("olf.deployment.cloud.provider.docker_health", lambda *args, **kwargs: pytest.fail("no docker"))

    provider.doctor(DeploymentPhase.FOUNDATION)

    assert required == ["terraform", "kubectl", "aws"]


def test_artifacts_doctor_requires_and_probes_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    backend = FakeCloudBackend(scope="aws", facts=_FACTS)
    provider = CloudProvider.create(config, backend, toolkit=_toolkit(), environ={})
    required: list[str] = []
    health_calls: list[str] = []
    monkeypatch.setattr(
        "olf.deployment.cloud.provider.base_report",
        lambda **kwargs: required.extend(kwargs["required_tools"]) or [],
    )
    monkeypatch.setattr(
        "olf.deployment.cloud.provider.docker_health",
        lambda *args, **kwargs: health_calls.append("docker") or None,
    )

    provider.doctor(DeploymentPhase.ARTIFACTS)

    assert required == ["terraform", "kubectl", "aws", "docker"]
    assert health_calls == ["docker"]


def test_full_platform_doctor_requires_and_probes_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    backend = FakeCloudBackend(scope="aws", facts=_FACTS)
    provider = CloudProvider.create(config, backend, toolkit=_toolkit(), environ={})
    required: list[str] = []
    health_calls: list[str] = []
    monkeypatch.setattr(
        "olf.deployment.cloud.provider.base_report",
        lambda **kwargs: required.extend(kwargs["required_tools"]) or [],
    )
    monkeypatch.setattr(
        "olf.deployment.cloud.provider.docker_health",
        lambda *args, **kwargs: health_calls.append("docker") or None,
    )

    provider.doctor(DeploymentPhase.PLATFORM)

    assert required == ["terraform", "kubectl", "aws", "helm", "docker"]
    assert health_calls == ["docker"]
