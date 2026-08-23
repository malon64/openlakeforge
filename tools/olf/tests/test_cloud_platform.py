from __future__ import annotations

from pathlib import Path

from _cloud_support import FakeCloudBackend
from _tooling_support import RecordedCall, RecordingRunner

from olf.deployment.cloud import platform
from olf.deployment.cloud.backend import FoundationFacts
from olf.deployment.cloud.config import CloudDeploymentConfig
from olf.deployment.context import DeploymentContext, Profile
from olf.deployment.engine import Toolkit
from olf.tooling.process import CommandResult
from olf.tooling.resolver import PathExecutableResolver

_TOOLS = ("terraform", "docker", "kind", "kubectl", "helm", "aws", "az")
_FACTS = FoundationFacts(
    cluster_name="eks-openlakeforge-poc",
    kube_context="eks-openlakeforge-poc",
    project_code_repository="123.dkr.ecr.eu-west-1.amazonaws.com/project-code",
    superset_repository="123.dkr.ecr.eu-west-1.amazonaws.com/superset",
    aws_region="eu-west-1",
)


def _config(tmp_path: Path, *, enable_analytics: str = "true") -> CloudDeploymentConfig:
    profile = Profile.FULL if enable_analytics == "true" else Profile.SLIM
    context = DeploymentContext.aws(repo_root=tmp_path, profile=profile)
    return CloudDeploymentConfig.from_environment({}, context=context)


def _toolkit(runner: RecordingRunner) -> Toolkit:
    from olf.tooling.aws import AwsCli
    from olf.tooling.azure import AzureCli
    from olf.tooling.docker import Docker
    from olf.tooling.helm import Helm
    from olf.tooling.kind import Kind
    from olf.tooling.kubectl import Kubectl
    from olf.tooling.terraform import Terraform

    resolver = PathExecutableResolver(overrides={tool: Path(tool) for tool in _TOOLS})
    return Toolkit(
        runner=runner,
        resolver=resolver,
        terraform=Terraform(runner, resolver),
        helm=Helm(runner, resolver),
        kubectl=Kubectl(runner, resolver),
        docker=Docker(runner, resolver),
        kind=Kind(runner, resolver),
        aws=AwsCli(runner, resolver),
        azure=AzureCli(runner, resolver),
    )


def _ok(stdout: str = "") -> CommandResult:
    return CommandResult(argv=(), returncode=0, stdout=stdout, stderr="", duration_seconds=0.0)


def _fail() -> CommandResult:
    return CommandResult(argv=(), returncode=1, stdout="", stderr="", duration_seconds=0.0)


class _PlatformScriptedRunner(RecordingRunner):
    """Chart cache hits (`helm show chart` succeeds) so tests exercise orchestration, not chart caching."""

    def __init__(self, *, namespace_exists: bool = False) -> None:
        super().__init__()
        self._namespace_exists = namespace_exists

    def run(self, command, **kwargs):  # type: ignore[override]
        argv = list(command.argv) if hasattr(command, "argv") else [str(p) for p in command]
        self.calls.append(RecordedCall(argv=argv, kwargs=kwargs))
        if argv[0] == "helm" and argv[1:3] == ["show", "chart"]:
            return _ok()
        if argv[0] == "terraform" and "state" in argv:
            return _ok() if self._namespace_exists else _fail()
        if argv[0] == "kubectl" and "namespace" in argv and "get" in argv:
            return _ok() if self._namespace_exists else _fail()
        return _ok()


def test_platform_up_skips_superset_build_when_analytics_disabled(tmp_path: Path) -> None:
    config = _config(tmp_path, enable_analytics="false")
    config.paths.helm_cache_dir.mkdir(parents=True, exist_ok=True)
    config.charts.trino_package_path.write_text("cached")
    config.charts.dagster_package_path.write_text("cached")
    backend = FakeCloudBackend(scope="aws")
    tools = _toolkit(_PlatformScriptedRunner())

    platform.platform_up(config, tools, backend, _FACTS, env={})

    assert "registry_login" not in backend.calls


def test_platform_up_builds_superset_when_analytics_enabled(tmp_path: Path) -> None:
    config = _config(tmp_path, enable_analytics="true")
    config.paths.helm_cache_dir.mkdir(parents=True, exist_ok=True)
    config.charts.trino_package_path.write_text("cached")
    config.charts.dagster_package_path.write_text("cached")
    backend = FakeCloudBackend(scope="aws")
    tools = _toolkit(_PlatformScriptedRunner())

    platform.platform_up(config, tools, backend, _FACTS, env={})

    assert "registry_login" in backend.calls


def test_platform_up_uses_backend_apply_variables_and_var_file(tmp_path: Path) -> None:
    config = _config(tmp_path, enable_analytics="false")
    config.paths.helm_cache_dir.mkdir(parents=True, exist_ok=True)
    config.charts.trino_package_path.write_text("cached")
    config.charts.dagster_package_path.write_text("cached")
    backend = FakeCloudBackend(scope="aws")
    runner = _PlatformScriptedRunner()
    tools = _toolkit(runner)

    platform.platform_up(config, tools, backend, _FACTS, env={})

    assert "platform_apply_variables" in backend.calls
    apply_call = next(c for c in runner.calls if c.argv[0] == "terraform" and c.argv[2:3] == ["apply"])
    assert "-var=namespace=lakehouse" in apply_call.argv


def test_platform_up_cleans_up_polaris_jobs_only_when_backend_requests_it(tmp_path: Path) -> None:
    config = _config(tmp_path, enable_analytics="false")
    config.paths.helm_cache_dir.mkdir(parents=True, exist_ok=True)
    config.charts.trino_package_path.write_text("cached")
    config.charts.dagster_package_path.write_text("cached")

    backend_no_cleanup = FakeCloudBackend(scope="aws", cleanup_polaris=False)
    tools = _toolkit(_PlatformScriptedRunner())
    platform.platform_up(config, tools, backend_no_cleanup, _FACTS, env={})
    assert not any("get" in c.argv and "jobs" in c.argv for c in tools.runner.calls)

    backend_cleanup = FakeCloudBackend(scope="azure", cleanup_polaris=True)
    tools2 = _toolkit(_PlatformScriptedRunner())
    platform.platform_up(config, tools2, backend_cleanup, _FACTS, env={})
    assert any("get" in c.argv and "jobs" in c.argv for c in tools2.runner.calls)
