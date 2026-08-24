from __future__ import annotations

from pathlib import Path

import pytest
from _tooling_support import RecordedCall, RecordingRunner

from olf.deployment.context import DeploymentContext, Profile
from olf.deployment.engine import Toolkit
from olf.deployment.errors import DeploymentPreconditionError, ExecutableNotFoundError
from olf.deployment.local import teardown
from olf.deployment.local.config import LocalDeploymentConfig
from olf.tooling.process import CommandResult
from olf.tooling.resolver import PathExecutableResolver

_TOOLS = ("terraform", "docker", "kind", "kubectl", "helm")


def _config(tmp_path: Path, *, profile: Profile = Profile.FULL) -> LocalDeploymentConfig:
    context = DeploymentContext.local(repo_root=tmp_path, profile=profile)
    config = LocalDeploymentConfig.from_environment({}, context=context)
    config.paths.foundation_state_path.parent.mkdir(parents=True, exist_ok=True)
    config.paths.foundation_state_path.write_text("{}")
    return config


def _toolkit(runner: RecordingRunner) -> Toolkit:
    from olf.tooling.aws import AwsCli
    from olf.tooling.azure import AzureCli
    from olf.tooling.docker import Docker
    from olf.tooling.helm import Helm
    from olf.tooling.kind import Kind
    from olf.tooling.kubectl import Kubectl
    from olf.tooling.terraform import Terraform

    resolver = PathExecutableResolver(overrides={t: Path(t) for t in _TOOLS})
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


def test_platform_down_raises_when_context_unreachable(tmp_path: Path) -> None:
    config = _config(tmp_path)
    runner = RecordingRunner(_fail())
    tools = _toolkit(runner)

    with pytest.raises(DeploymentPreconditionError, match="not reachable"):
        teardown.platform_down(config, tools, env={})


def test_platform_down_raises_when_foundation_state_missing(tmp_path: Path) -> None:
    context = DeploymentContext.local(repo_root=tmp_path)
    config = LocalDeploymentConfig.from_environment({}, context=context)
    runner = RecordingRunner(_ok())
    tools = _toolkit(runner)

    with pytest.raises(DeploymentPreconditionError, match="foundation Terraform state is missing"):
        teardown.platform_down(config, tools, env={})


class _TeardownScriptedRunner(RecordingRunner):
    def __init__(self, *, helm_releases_present: set[str] | None = None) -> None:
        super().__init__()
        self._present = helm_releases_present or set()

    def run(self, command, **kwargs):  # type: ignore[override]
        argv = list(command.argv) if hasattr(command, "argv") else [str(p) for p in command]
        self.calls.append(RecordedCall(argv=argv, kwargs=kwargs))
        if argv[0] == "helm" and "status" in argv:
            release = argv[-3]
            return _ok() if release in self._present else _fail()
        return _ok()


def test_platform_down_orders_superset_job_delete_before_destroy_and_namespace_delete_last(tmp_path: Path) -> None:
    config = _config(tmp_path)
    runner = _TeardownScriptedRunner()
    tools = _toolkit(runner)

    teardown.platform_down(config, tools, env={})

    kinds = []
    for call in runner.calls:
        if "superset-init-db" in call.argv:
            kinds.append("superset-job-delete")
        elif call.argv[0] == "terraform" and "destroy" in call.argv:
            kinds.append("terraform-destroy")
        elif "namespace" in call.argv and "delete" in call.argv:
            kinds.append("namespace-delete")
    assert kinds == ["superset-job-delete", "terraform-destroy", "namespace-delete"]


def test_platform_down_destroy_uses_four_var_subset(tmp_path: Path) -> None:
    config = _config(tmp_path)
    runner = _TeardownScriptedRunner()
    tools = _toolkit(runner)

    teardown.platform_down(config, tools, env={})

    destroy_call = next(c for c in runner.calls if c.argv[0] == "terraform" and "destroy" in c.argv)
    assert "-var=namespace=lakehouse" in destroy_call.argv
    assert not any("project_code_image" in arg for arg in destroy_call.argv)
    assert not any("trino_chart_package_path" in arg for arg in destroy_call.argv)


def test_cleanup_legacy_helm_releases_only_uninstalls_present_releases(tmp_path: Path) -> None:
    config = _config(tmp_path)
    runner = _TeardownScriptedRunner(helm_releases_present={"trino", "garage"})
    tools = _toolkit(runner)

    removed = teardown.cleanup_legacy_helm_releases(config, tools, env={})

    assert removed == ["trino", "garage"]
    uninstall_releases = [c.argv[c.argv.index("uninstall") + 1] for c in runner.calls if "uninstall" in c.argv]
    assert uninstall_releases == ["trino", "garage"]


def test_cleanup_legacy_helm_releases_skips_when_helm_missing(tmp_path: Path) -> None:
    config = _config(tmp_path)

    class MissingHelmRunner(RecordingRunner):
        def run(self, command, **kwargs):  # type: ignore[override]
            raise ExecutableNotFoundError("helm")

    resolver = PathExecutableResolver(overrides={"terraform": Path("terraform"), "kubectl": Path("kubectl")})
    from olf.tooling.aws import AwsCli
    from olf.tooling.azure import AzureCli
    from olf.tooling.docker import Docker
    from olf.tooling.helm import Helm
    from olf.tooling.kind import Kind
    from olf.tooling.kubectl import Kubectl
    from olf.tooling.terraform import Terraform

    runner = MissingHelmRunner()
    tools = Toolkit(
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

    removed = teardown.cleanup_legacy_helm_releases(config, tools, env={})

    assert removed == []
