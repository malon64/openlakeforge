from __future__ import annotations

from pathlib import Path

import pytest
from _tooling_support import RecordedCall, RecordingRunner

from olf.deployment.context import DeploymentContext, Profile
from olf.deployment.engine import Toolkit
from olf.deployment.errors import DeploymentPreconditionError
from olf.deployment.local import images
from olf.deployment.local.config import LocalDeploymentConfig
from olf.tooling.process import CommandResult
from olf.tooling.resolver import PathExecutableResolver


def _config(tmp_path: Path, *, profile: Profile = Profile.FULL, environ: dict | None = None) -> LocalDeploymentConfig:
    context = DeploymentContext.local(repo_root=tmp_path, profile=profile)
    return LocalDeploymentConfig.from_environment(environ or {}, context=context)


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


def _ok(stdout: str = "") -> CommandResult:
    return CommandResult(argv=(), returncode=0, stdout=stdout, stderr="", duration_seconds=0.0)


class _ScriptedRunner(RecordingRunner):
    def __init__(self, *, image_exists: bool = True, cluster_exists: bool = True) -> None:
        super().__init__()
        self._image_exists = image_exists
        self._cluster_exists = cluster_exists

    def run(self, command, **kwargs):  # type: ignore[override]
        argv = list(command.argv) if hasattr(command, "argv") else [str(p) for p in command]
        self.calls.append(RecordedCall(argv=argv, kwargs=kwargs))
        if argv[1:3] == ["image", "inspect"]:
            code = 0 if self._image_exists else 1
            return CommandResult(argv=(), returncode=code, stdout="", stderr="", duration_seconds=0.0)
        if argv[1:3] == ["get", "clusters"]:
            stdout = "openlakeforge-local\n" if self._cluster_exists else ""
            return CommandResult(argv=(), returncode=0, stdout=stdout, stderr="", duration_seconds=0.0)
        return _ok()


def test_build_superset_image_builds_with_base_arg(tmp_path: Path) -> None:
    config = _config(tmp_path)
    runner = RecordingRunner(_ok())
    tools = _toolkit(runner)

    image = images.build_superset_image(config, tools, env={})

    assert image == "ghcr.io/openlakeforge/superset:local"
    build_call = next(c for c in runner.calls if c.argv[1] == "build")
    assert "--build-arg" in build_call.argv
    assert f"SUPERSET_BASE_IMAGE={config.images.superset_base_image}" in build_call.argv
    assert build_call.argv[2] == str(config.paths.repo_root / "images/superset")


def test_build_project_code_image_includes_revision_build_arg(tmp_path: Path) -> None:
    config = _config(tmp_path)
    runner = RecordingRunner(_ok())
    tools = _toolkit(runner)

    image = images.build_project_code_image(config, tools, env={}, revision="rev-123")

    assert image == "ghcr.io/openlakeforge/project-code:local"
    build_call = next(c for c in runner.calls if c.argv[1] == "build")
    assert "FLOE_MANIFEST_REVISION=rev-123" in build_call.argv
    assert build_call.argv[2] == str(config.paths.repo_root)


def test_load_image_into_kind_raises_when_image_missing(tmp_path: Path) -> None:
    config = _config(tmp_path)
    tools = _toolkit(_ScriptedRunner(image_exists=False))

    with pytest.raises(DeploymentPreconditionError, match="does not exist"):
        images.load_image_into_kind("ghcr.io/openlakeforge/project-code:local", config, tools, env={})


def test_load_image_into_kind_raises_when_cluster_missing(tmp_path: Path) -> None:
    config = _config(tmp_path)
    tools = _toolkit(_ScriptedRunner(image_exists=True, cluster_exists=False))

    with pytest.raises(DeploymentPreconditionError, match="does not exist"):
        images.load_image_into_kind("ghcr.io/openlakeforge/project-code:local", config, tools, env={})


def test_load_image_into_kind_loads_when_present(tmp_path: Path) -> None:
    config = _config(tmp_path)
    runner = _ScriptedRunner(image_exists=True, cluster_exists=True)
    tools = _toolkit(runner)

    images.load_image_into_kind("ghcr.io/openlakeforge/project-code:local", config, tools, env={})

    assert any(c.argv[:3] == ["kind", "load", "docker-image"] for c in runner.calls)


def test_prepare_superset_image_skips_when_analytics_disabled(tmp_path: Path) -> None:
    config = _config(tmp_path, profile=Profile.SLIM)
    runner = _ScriptedRunner()
    tools = _toolkit(runner)

    images.prepare_superset_image(config, tools, env={})

    assert runner.calls == []


def test_prepare_superset_image_skips_when_tag_is_not_local(tmp_path: Path) -> None:
    config = _config(tmp_path, environ={"SUPERSET_IMAGE_TAG": "v1.0.0"})
    runner = _ScriptedRunner()
    tools = _toolkit(runner)

    images.prepare_superset_image(config, tools, env={})

    assert runner.calls == []


def test_prepare_superset_image_builds_and_loads_when_enabled_and_local(tmp_path: Path) -> None:
    config = _config(tmp_path)
    runner = _ScriptedRunner(image_exists=True, cluster_exists=True)
    tools = _toolkit(runner)

    images.prepare_superset_image(config, tools, env={})

    assert any(c.argv[1] == "build" for c in runner.calls)
    assert any(c.argv[:3] == ["kind", "load", "docker-image"] for c in runner.calls)


def test_prepare_project_code_image_skips_when_tag_is_not_local(tmp_path: Path) -> None:
    config = _config(tmp_path, environ={"PROJECT_CODE_IMAGE_TAG": "v1"})
    runner = _ScriptedRunner()
    tools = _toolkit(runner)

    images.prepare_project_code_image(config, tools, env={}, revision="rev-1")

    assert runner.calls == []


def test_prepare_project_code_image_builds_and_loads(tmp_path: Path) -> None:
    config = _config(tmp_path)
    runner = _ScriptedRunner(image_exists=True, cluster_exists=True)
    tools = _toolkit(runner)

    images.prepare_project_code_image(config, tools, env={}, revision="rev-1")

    build_call = next(c for c in runner.calls if c.argv[1] == "build")
    assert "FLOE_MANIFEST_REVISION=rev-1" in build_call.argv
    assert any(c.argv[:3] == ["kind", "load", "docker-image"] for c in runner.calls)
