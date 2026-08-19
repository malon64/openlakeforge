from __future__ import annotations

from pathlib import Path

from _tooling_support import RecordingRunner

from olf.deployment.engine import Toolkit
from olf.deployment.local import floe_manifests
from olf.deployment.local.config import FloeManifestSettings
from olf.tooling.process import CommandResult
from olf.tooling.resolver import PathExecutableResolver


def _toolkit(runner: RecordingRunner) -> Toolkit:
    from olf.tooling.docker import Docker
    from olf.tooling.helm import Helm
    from olf.tooling.kind import Kind
    from olf.tooling.kubectl import Kubectl
    from olf.tooling.terraform import Terraform

    resolver = PathExecutableResolver(
        overrides={t: Path(t) for t in ("terraform", "docker", "kind", "kubectl", "helm")}
    )
    return Toolkit(
        runner=runner,
        resolver=resolver,
        terraform=Terraform(runner, resolver),
        helm=Helm(runner, resolver),
        kubectl=Kubectl(runner, resolver),
        docker=Docker(runner, resolver),
        kind=Kind(runner, resolver),
    )


def _make_repo(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    (repo_root / "domains/orders/contracts/floe").mkdir(parents=True)
    (repo_root / "domains/orders/contracts/floe/orders.yml").write_text("sources: []\n")
    (repo_root / "libs/floe/profiles").mkdir(parents=True)
    (repo_root / "libs/floe/profiles/local-k8s.yml").write_text("apiVersion: floe/v1\n")
    return repo_root


def _settings(repo_root: Path) -> FloeManifestSettings:
    return FloeManifestSettings(
        version="0.6.11",
        image="ghcr.io/malon64/floe:0.6.11",
        runtime="image",
        runtime_artifact_dir=repo_root / ".tmp/floe-runtime/local",
        platform=None,
    )


def test_discover_floe_configs_finds_domain_configs(tmp_path: Path) -> None:
    repo_root = _make_repo(tmp_path)

    configs = floe_manifests.discover_floe_configs(repo_root)

    assert configs == [repo_root / "domains/orders/contracts/floe/orders.yml"]


def test_generate_local_manifests_uses_checked_in_profile_when_governance_enabled(tmp_path: Path) -> None:
    repo_root = _make_repo(tmp_path)
    settings = _settings(repo_root)
    runner = RecordingRunner(CommandResult(argv=(), returncode=0, stdout="", stderr="", duration_seconds=0.0))
    tools = _toolkit(runner)

    manifests = floe_manifests.generate_local_manifests(
        settings,
        tools,
        repo_root=repo_root,
        namespace="lakehouse",
        governance_enabled=True,
        environ={},
        env={},
    )

    assert manifests == [settings.runtime_artifact_dir / "manifests/orders/orders/orders.manifest.json"]
    profile_copy = settings.runtime_artifact_dir / "profiles/orders/orders/local-k8s.yml"
    assert profile_copy.read_text() == "apiVersion: floe/v1\n"
    config_copy = settings.runtime_artifact_dir / "configs/orders/orders/orders.yml"
    assert config_copy.read_text() == "sources: []\n"


def test_generate_local_manifests_renders_profile_when_governance_disabled(tmp_path: Path) -> None:
    repo_root = _make_repo(tmp_path)
    settings = _settings(repo_root)
    runner = RecordingRunner(CommandResult(argv=(), returncode=0, stdout="", stderr="", duration_seconds=0.0))
    tools = _toolkit(runner)

    floe_manifests.generate_local_manifests(
        settings,
        tools,
        repo_root=repo_root,
        namespace="lakehouse",
        governance_enabled=False,
        environ={"OPENLAKEFORGE_GOVERNANCE_ENABLED": "false"},
        env={},
    )

    rendered_profile = settings.runtime_artifact_dir / "profiles/local-k8s.yml"
    assert rendered_profile.is_file()
    assert "EnvironmentProfile" in rendered_profile.read_text()


def test_generate_local_manifests_runs_validate_then_generate_via_docker(tmp_path: Path) -> None:
    repo_root = _make_repo(tmp_path)
    settings = _settings(repo_root)
    runner = RecordingRunner(CommandResult(argv=(), returncode=0, stdout="", stderr="", duration_seconds=0.0))
    tools = _toolkit(runner)

    floe_manifests.generate_local_manifests(
        settings, tools, repo_root=repo_root, namespace="lakehouse", governance_enabled=True, environ={}, env={}
    )

    subcommands = [call.argv[call.argv.index(settings.image) + 1] for call in runner.calls]
    assert subcommands == ["validate", "manifest"]
    validate_call = runner.calls[0]
    assert "-v" in validate_call.argv
    assert f"{repo_root}:/work" in validate_call.argv
    assert "-c" in validate_call.argv
    generate_call = runner.calls[1]
    assert "--deterministic" in generate_call.argv
    assert "--manifest-name" in generate_call.argv
    assert "orders.orders.local" in generate_call.argv


def test_generate_local_manifests_raises_when_no_configs_found(tmp_path: Path) -> None:
    from olf.deployment.errors import DeploymentPreconditionError

    repo_root = tmp_path / "empty-repo"
    (repo_root / "domains").mkdir(parents=True)
    (repo_root / "libs/floe/profiles").mkdir(parents=True)
    (repo_root / "libs/floe/profiles/local-k8s.yml").write_text("apiVersion: floe/v1\n")
    settings = _settings(repo_root)
    tools = _toolkit(RecordingRunner())

    import pytest

    with pytest.raises(DeploymentPreconditionError):
        floe_manifests.generate_local_manifests(
            settings, tools, repo_root=repo_root, namespace="lakehouse", governance_enabled=True, environ={}, env={}
        )
