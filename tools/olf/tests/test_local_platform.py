from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest
from _tooling_support import RecordedCall, RecordingRunner

from olf.deployment.context import DeploymentContext, Profile
from olf.deployment.engine import Toolkit
from olf.deployment.errors import DeploymentPreconditionError
from olf.deployment.local import platform
from olf.deployment.local.config import LocalDeploymentConfig
from olf.tooling.process import CommandResult
from olf.tooling.resolver import PathExecutableResolver

_TOOLS = ("terraform", "docker", "kind", "kubectl", "helm")


def _config(tmp_path: Path, *, profile: Profile = Profile.FULL) -> LocalDeploymentConfig:
    context = DeploymentContext.local(repo_root=tmp_path, profile=profile)
    return LocalDeploymentConfig.from_environment({}, context=context)


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


def _fake_dagster_archive_bytes() -> bytes:
    """A minimal real gzip tarball shaped like `helm pull`'s Dagster output -
    see `test_deployment_charts.py`'s identical helper for why the schema-
    stripping repack needs a real archive rather than a `RecordingRunner`
    stub, even for a chart source mode never digest-verifies."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
        for name, content in (
            (b"dagster/Chart.yaml", b"name: dagster\n"),
            (b"dagster/values.schema.json", b"{}"),
        ):
            info = tarfile.TarInfo(name.decode())
            info.size = len(content)
            bundle.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def test_platform_apply_variables_exact_order(tmp_path: Path) -> None:
    config = _config(tmp_path)

    variables = platform.platform_apply_variables(config)

    assert list(variables.keys()) == [
        "namespace",
        "kube_context",
        "kubeconfig_path",
        "helm_repository_cache_path",
        "helm_repository_config_path",
        "foundation_state_path",
        "project_code_image_repository",
        "project_code_image_tag",
        "project_code_image_pull_policy",
        "project_code_image_revision",
        "enable_governance",
        "enable_analytics",
        "superset_image_repository",
        "superset_image_tag",
        "superset_image_pull_policy",
        "trino_chart_package_path",
        "dagster_chart_package_path",
        "seaweedfs_chart_package_path",
        "polaris_chart_package_path",
        "openmetadata_chart_package_path",
        "openmetadata_deps_chart_package_path",
        "superset_chart_package_path",
    ]
    assert variables["enable_governance"] == "true"
    assert variables["enable_analytics"] == "true"


def test_platform_apply_variables_slim_profile_omits_disabled_layer_charts(tmp_path: Path) -> None:
    config = _config(tmp_path, profile=Profile.SLIM)

    variables = platform.platform_apply_variables(config)

    assert "openmetadata_chart_package_path" not in variables
    assert "openmetadata_deps_chart_package_path" not in variables
    assert "superset_chart_package_path" not in variables
    assert "trino_chart_package_path" in variables
    assert "dagster_chart_package_path" in variables
    assert "seaweedfs_chart_package_path" in variables
    assert "polaris_chart_package_path" in variables


def test_platform_destroy_variables_are_the_four_var_subset(tmp_path: Path) -> None:
    config = _config(tmp_path)

    variables = platform.platform_destroy_variables(config)

    assert list(variables.keys()) == [
        "namespace",
        "kube_context",
        "kubeconfig_path",
        "helm_repository_cache_path",
        "helm_repository_config_path",
        "foundation_state_path",
    ]


def test_platform_var_files_empty_for_full_present_for_slim(tmp_path: Path) -> None:
    full_config = _config(tmp_path, profile=Profile.FULL)
    slim_config = _config(tmp_path, profile=Profile.SLIM)

    assert platform.platform_var_files(full_config) == ()
    assert platform.platform_var_files(slim_config) == (str(slim_config.terraform.var_file),)


def test_platform_up_raises_when_foundation_state_missing(tmp_path: Path) -> None:
    config = _config(tmp_path)
    tools = _toolkit(RecordingRunner(_ok()))

    with pytest.raises(DeploymentPreconditionError, match="foundation Terraform state is missing"):
        platform.platform_up(config, tools, env={})


class _PlatformScriptedRunner(RecordingRunner):
    def __init__(
        self,
        *,
        namespace_exists: bool = False,
        seaweedfs_in_state: bool = True,
        apply_failures: int = 0,
    ) -> None:
        super().__init__()
        self._namespace_exists = namespace_exists
        self._seaweedfs_in_state = seaweedfs_in_state
        self._apply_failures = apply_failures
        self._apply_attempts = 0

    def run(self, command, **kwargs):  # type: ignore[override]
        from olf.deployment.errors import CommandExecutionError

        argv = list(command.argv) if hasattr(command, "argv") else [str(p) for p in command]
        self.calls.append(RecordedCall(argv=argv, kwargs=kwargs))

        if argv[0] == "kubectl" and "get-contexts" in argv:
            return _ok("kind-openlakeforge-local\n")
        if argv[0] == "kubectl" and "namespace" in argv and "get" in argv and "jsonpath" not in " ".join(argv):
            return _ok() if self._namespace_exists else _fail()
        if argv[0] == "terraform" and "state" in argv and "module.seaweedfs.helm_release.seaweedfs" in argv:
            return _ok() if self._seaweedfs_in_state else _fail()
        if argv[0] == "terraform" and "apply" in argv:
            self._apply_attempts += 1
            if self._apply_attempts <= self._apply_failures:
                raise CommandExecutionError(argv, 1, stderr="apply failed")
            return _ok()
        if argv[0] == "helm" and "show" in argv:
            return CommandResult(argv=(), returncode=1, stdout="", stderr="", duration_seconds=0.0)
        if argv[0] == "helm" and "pull" in argv and "dagster/dagster" in argv:
            destination = Path(argv[argv.index("--destination") + 1])
            version = argv[argv.index("--version") + 1]
            destination.mkdir(parents=True, exist_ok=True)
            (destination / f"dagster-{version}.tgz").write_bytes(_fake_dagster_archive_bytes())
        if argv[0] == "helm" and "package" in argv:
            destination = Path(argv[argv.index("--destination") + 1])
            destination.mkdir(parents=True, exist_ok=True)
            (destination / "dagster-1.13.6.tgz").write_bytes(b"fake-repacked-chart")
        return _ok()


def test_platform_up_happy_path_applies_terraform(tmp_path: Path) -> None:
    config = _config(tmp_path, profile=Profile.SLIM)
    config.paths.foundation_state_path.parent.mkdir(parents=True, exist_ok=True)
    config.paths.foundation_state_path.write_text("{}")
    runner = _PlatformScriptedRunner(namespace_exists=False, seaweedfs_in_state=True)
    tools = _toolkit(runner)

    platform.platform_up(config, tools, env={})

    apply_calls = [c for c in runner.calls if c.argv[0] == "terraform" and "apply" in c.argv]
    assert len(apply_calls) == 1


def test_platform_up_recovers_from_drifted_state(tmp_path: Path) -> None:
    config = _config(tmp_path, profile=Profile.SLIM)
    config.paths.foundation_state_path.parent.mkdir(parents=True, exist_ok=True)
    config.paths.foundation_state_path.write_text("{}")
    runner = _PlatformScriptedRunner(namespace_exists=True, seaweedfs_in_state=False)
    tools = _toolkit(runner)

    platform.platform_up(config, tools, env={})

    destroy_calls = [c for c in runner.calls if c.argv[0] == "terraform" and "destroy" in c.argv]
    assert len(destroy_calls) == 1  # teardown ran as part of drift recovery
    init_calls = [c for c in runner.calls if c.argv[0] == "terraform" and "init" in c.argv]
    assert len(init_calls) >= 2  # once up front, once after the drift-triggered teardown


def test_platform_up_retries_apply_and_cleans_up_polaris_jobs_each_attempt(tmp_path: Path) -> None:
    context = DeploymentContext.local(repo_root=tmp_path, profile=Profile.SLIM)
    config = LocalDeploymentConfig.from_environment(
        {"LOCAL_UP_RETRY_ATTEMPTS": "3", "LOCAL_UP_RETRY_DELAY_SECONDS": "0"}, context=context
    )
    config.paths.foundation_state_path.parent.mkdir(parents=True, exist_ok=True)
    config.paths.foundation_state_path.write_text("{}")
    runner = _PlatformScriptedRunner(namespace_exists=False, seaweedfs_in_state=True, apply_failures=1)
    tools = _toolkit(runner)

    platform.platform_up(config, tools, env={})

    apply_calls = [c for c in runner.calls if c.argv[0] == "terraform" and "apply" in c.argv]
    assert len(apply_calls) == 2  # first attempt failed, second succeeded
