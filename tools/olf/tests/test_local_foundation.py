from __future__ import annotations

from pathlib import Path

import pytest
from _tooling_support import RecordingRunner

from olf.deployment.context import DeploymentContext
from olf.deployment.engine import Toolkit
from olf.deployment.errors import DeploymentPreconditionError
from olf.deployment.local import foundation
from olf.deployment.local.config import LocalDeploymentConfig
from olf.tooling.process import CommandResult
from olf.tooling.resolver import PathExecutableResolver

_TOOLS = ("terraform", "docker", "kind", "kubectl", "helm")


def _config(tmp_path: Path) -> LocalDeploymentConfig:
    context = DeploymentContext.local(repo_root=tmp_path)
    return LocalDeploymentConfig.from_environment({}, context=context)


def _toolkit_with_runner(runner: RecordingRunner) -> Toolkit:
    resolver = PathExecutableResolver(overrides={tool: Path(tool) for tool in _TOOLS})
    from olf.tooling.aws import AwsCli
    from olf.tooling.azure import AzureCli
    from olf.tooling.docker import Docker
    from olf.tooling.helm import Helm
    from olf.tooling.kind import Kind
    from olf.tooling.kubectl import Kubectl
    from olf.tooling.terraform import Terraform

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


def _fail(stderr: str = "") -> CommandResult:
    return CommandResult(argv=(), returncode=1, stdout="", stderr=stderr, duration_seconds=0.0)


class _ScriptedRunner(RecordingRunner):
    """Returns a scripted result based on a predicate over the argv."""

    def __init__(self, rules: list[tuple[callable, CommandResult]], default: CommandResult) -> None:
        super().__init__()
        self._rules = rules
        self._default = default

    def run(self, command, **kwargs):  # type: ignore[override]
        from _tooling_support import RecordedCall

        argv = list(command.argv) if hasattr(command, "argv") else [str(p) for p in command]
        self.calls.append(RecordedCall(argv=argv, kwargs=kwargs))
        for predicate, result in self._rules:
            if predicate(argv):
                return result
        return self._default


def test_foundation_apply_variables_exact_order_and_content(tmp_path: Path) -> None:
    config = _config(tmp_path)

    variables = foundation.foundation_apply_variables(config)

    assert list(variables.keys()) == [
        "cluster_name",
        "cluster_config_path",
        "kubeconfig_path",
        "kind_wait_timeout",
        "reset_existing_cluster",
    ]
    assert variables["cluster_name"] == "openlakeforge-local"
    assert variables["reset_existing_cluster"] == "false"


def test_foundation_destroy_variables_are_the_three_var_subset(tmp_path: Path) -> None:
    config = _config(tmp_path)

    variables = foundation.foundation_destroy_variables(config)

    assert list(variables.keys()) == ["cluster_name", "cluster_config_path", "kubeconfig_path"]


def test_foundation_up_applies_exports_kubeconfig_and_checks_reachability(tmp_path: Path) -> None:
    config = _config(tmp_path)
    runner = _ScriptedRunner(
        rules=[
            (lambda argv: "get-contexts" in argv, _ok("kind-openlakeforge-local\n")),
        ],
        default=_ok(),
    )
    tools = _toolkit_with_runner(runner)

    foundation.foundation_up(config, tools, env={})

    assert ["terraform", "-chdir=" + str(config.paths.foundation_terraform_dir), "init"] == runner.calls[0].argv
    assert runner.calls[1].argv[2] == "apply"
    export_call = next(c for c in runner.calls if "export" in c.argv)
    assert export_call.argv[:3] == ["kind", "export", "kubeconfig"]
    assert any("get-contexts" in c.argv for c in runner.calls)
    assert any("cluster-info" in c.argv for c in runner.calls)


def test_foundation_down_is_idempotent_when_nothing_exists(tmp_path: Path) -> None:
    config = _config(tmp_path)
    runner = _ScriptedRunner(
        rules=[
            (lambda argv: "state" in argv, _fail()),
            (lambda argv: argv[:2] == ["kind", "get"], _ok("")),
        ],
        default=_ok(),
    )
    tools = _toolkit_with_runner(runner)

    foundation.foundation_down(config, tools, env={})

    assert not any(c.argv[1:2] == ["destroy"] for c in runner.calls)


def test_foundation_down_refuses_to_destroy_unmanaged_cluster(tmp_path: Path) -> None:
    config = _config(tmp_path)
    runner = _ScriptedRunner(
        rules=[
            (lambda argv: "state" in argv, _fail()),
            (lambda argv: argv[:2] == ["kind", "get"], _ok("openlakeforge-local\n")),
        ],
        default=_ok(),
    )
    tools = _toolkit_with_runner(runner)

    with pytest.raises(DeploymentPreconditionError, match="does not own it"):
        foundation.foundation_down(config, tools, env={})


def test_foundation_down_refuses_when_namespace_still_present(tmp_path: Path) -> None:
    config = _config(tmp_path)
    runner = _ScriptedRunner(
        rules=[
            (lambda argv: "state" in argv, _ok()),
            (lambda argv: argv[:2] == ["kind", "get"], _ok("openlakeforge-local\n")),
            (lambda argv: "namespace" in argv and "get" in argv, _ok()),
        ],
        default=_ok(),
    )
    tools = _toolkit_with_runner(runner)

    with pytest.raises(DeploymentPreconditionError, match="still exists"):
        foundation.foundation_down(config, tools, env={})


def test_foundation_down_force_overrides_namespace_check(tmp_path: Path) -> None:
    config = _config(tmp_path)
    runner = _ScriptedRunner(
        rules=[
            (lambda argv: "state" in argv, _ok()),
            (lambda argv: argv[:2] == ["kind", "get"], _ok("openlakeforge-local\n")),
            (lambda argv: "namespace" in argv and "get" in argv, _ok()),
        ],
        default=_ok(),
    )
    tools = _toolkit_with_runner(runner)

    foundation.foundation_down(config, tools, env={}, force=True)

    assert any(c.argv[2:3] == ["destroy"] for c in runner.calls if c.argv[0] == "terraform")


def test_foundation_down_destroys_when_no_platform_resources_remain(tmp_path: Path) -> None:
    config = _config(tmp_path)
    runner = _ScriptedRunner(
        rules=[
            (lambda argv: "state" in argv, _ok()),
            (lambda argv: argv[:2] == ["kind", "get"], _ok("openlakeforge-local\n")),
            (lambda argv: "namespace" in argv and "get" in argv, _fail()),
        ],
        default=_ok(),
    )
    tools = _toolkit_with_runner(runner)

    foundation.foundation_down(config, tools, env={})

    destroy_calls = [c for c in runner.calls if c.argv[0] == "terraform" and c.argv[2] == "destroy"]
    assert len(destroy_calls) == 1
    assert "-var=cluster_name=openlakeforge-local" in destroy_calls[0].argv
    assert not any("kind_wait_timeout" in arg for arg in destroy_calls[0].argv)
