from __future__ import annotations

from pathlib import Path

from _tooling_support import RecordedCall, RecordingRunner

from olf.deployment.context import DeploymentContext
from olf.deployment.engine import Toolkit
from olf.deployment.local.config import LocalDeploymentConfig
from olf.deployment.local.provider import LocalProvider
from olf.tooling.aws import AwsCli
from olf.tooling.azure import AzureCli
from olf.tooling.docker import Docker
from olf.tooling.helm import Helm
from olf.tooling.kind import Kind
from olf.tooling.kubectl import Kubectl
from olf.tooling.process import CommandResult
from olf.tooling.resolver import PathExecutableResolver
from olf.tooling.terraform import Terraform

_TOOLS = ("terraform", "docker", "kind", "kubectl", "helm")


def _toolkit() -> Toolkit:
    runner = RecordingRunner(CommandResult(argv=(), returncode=0, stdout="", stderr="", duration_seconds=0.0))
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


def _config(tmp_path: Path) -> LocalDeploymentConfig:
    context = DeploymentContext.local(repo_root=tmp_path)
    return LocalDeploymentConfig.from_environment({}, context=context)


def test_env_excludes_ambient_variables_not_scoped_by_the_deployment(tmp_path: Path) -> None:
    ambient = {
        "PATH": "/usr/bin",
        "DATABASE_URL": "postgres://prod-user:s3cr3t@db.internal/prod",
        "SENTRY_DSN": "https://public@sentry.example/1",
        "DOCKER_HOST": "unix:///var/run/docker.sock",
    }

    provider = LocalProvider.create(_config(tmp_path), toolkit=_toolkit(), environ=ambient)

    env = provider.env

    assert "DATABASE_URL" not in env
    assert "SENTRY_DSN" not in env
    assert "PATH" not in env


def test_env_still_carries_every_deployment_scoped_key(tmp_path: Path) -> None:
    # DOCKER_HOST left unset so the ambient-context resolution path (covered
    # separately below) doesn't need scripting here.
    provider = LocalProvider.create(_config(tmp_path), toolkit=_toolkit(), environ={})

    env = provider.env

    assert env["KUBECONFIG"] == str(provider.context.paths.kubeconfig_path)
    assert env["KUBE_CONTEXT"] == provider.context.kube_context
    assert env["DOCKER_CONFIG"] == str(provider.context.paths.docker_config_dir)
    assert env["HELM_REPOSITORY_CONFIG"] == str(provider.context.paths.helm_repository_config)
    assert env["HELM_REPOSITORY_CACHE"] == str(provider.context.paths.helm_repository_cache)
    assert env["SUPERSET_REPORT_WORK_DIR"] == str(provider.context.paths.superset_report_work_dir)
    assert env["OPENLAKEFORGE_PORT_FORWARD_LOG_PREFIX"] == str(provider.context.paths.port_forward_log_prefix)


def test_env_resolves_docker_host_from_ambient_context_when_unset(tmp_path: Path) -> None:
    class _ScriptedRunner(RecordingRunner):
        def run(self, command, **kwargs):  # type: ignore[override]
            argv = list(command.argv) if hasattr(command, "argv") else [str(p) for p in command]
            self.calls.append(RecordedCall(argv=argv, kwargs=kwargs))
            stdout = "colima\n" if argv[-2:] == ["context", "show"] else "unix:///colima/docker.sock\n"
            return CommandResult(argv=tuple(argv), returncode=0, stdout=stdout, stderr="", duration_seconds=0.0)

    runner = _ScriptedRunner()
    resolver = PathExecutableResolver(overrides={tool: Path(tool) for tool in _TOOLS})
    toolkit = Toolkit(
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

    provider = LocalProvider.create(_config(tmp_path), toolkit=toolkit, environ={})

    assert provider.env["DOCKER_HOST"] == "unix:///colima/docker.sock"
