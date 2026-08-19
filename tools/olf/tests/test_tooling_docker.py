from __future__ import annotations

from pathlib import Path

from _tooling_support import RecordedCall, RecordingRunner

from olf.tooling.docker import Docker
from olf.tooling.process import CommandResult
from olf.tooling.resolver import PathExecutableResolver


def _docker(result: CommandResult | None = None) -> tuple[Docker, RecordingRunner]:
    runner = RecordingRunner(result)
    resolver = PathExecutableResolver(overrides={"docker": Path("docker")})
    return Docker(runner, resolver), runner


def test_build_builds_exact_argv() -> None:
    docker, runner = _docker()

    docker.build(Path("/repo/images/project-code"), tag="project-code:local", build_args={"REVISION": "abc123"})

    assert runner.last_call.argv == [
        "docker",
        "build",
        "/repo/images/project-code",
        "-t",
        "project-code:local",
        "--build-arg",
        "REVISION=abc123",
    ]


def test_pull_and_push_build_expected_argv() -> None:
    docker, runner = _docker()

    docker.pull("ghcr.io/openlakeforge/project-code:local")
    assert runner.last_call.argv == ["docker", "pull", "ghcr.io/openlakeforge/project-code:local"]

    docker.push("ghcr.io/openlakeforge/project-code:local")
    assert runner.last_call.argv == ["docker", "push", "ghcr.io/openlakeforge/project-code:local"]


def test_tag_builds_expected_argv() -> None:
    docker, runner = _docker()

    docker.tag("project-code:local", "ghcr.io/openlakeforge/project-code:local")

    assert runner.last_call.argv == [
        "docker",
        "tag",
        "project-code:local",
        "ghcr.io/openlakeforge/project-code:local",
    ]


def test_login_uses_password_stdin_not_argv() -> None:
    docker, runner = _docker()

    docker.login("ghcr.io", username="foo", password="abc")

    assert runner.last_call.argv == ["docker", "login", "--username", "foo", "--password-stdin", "ghcr.io"]
    assert "abc" not in runner.last_call.argv
    assert runner.last_call.kwargs["input_text"] == "abc"


def test_context_show_and_inspect() -> None:
    docker, runner = _docker(CommandResult(argv=(), returncode=0, stdout="colima\n", stderr="", duration_seconds=0.0))

    name = docker.context_show()

    assert name == "colima"
    assert runner.last_call.argv == ["docker", "context", "show"]


def test_resolve_current_engine_endpoint_chains_show_and_inspect() -> None:
    class ScriptedRunner(RecordingRunner):
        def run(self, command, **kwargs):  # type: ignore[override]
            argv = list(command.argv) if hasattr(command, "argv") else [str(p) for p in command]
            self.calls.append(RecordedCall(argv=argv, kwargs=kwargs))
            if argv[-2:] == ["context", "show"]:
                stdout = "colima\n"
            else:
                stdout = "unix:///Users/dev/.colima/default/docker.sock\n"
            return CommandResult(argv=tuple(argv), returncode=0, stdout=stdout, stderr="", duration_seconds=0.0)

    runner = ScriptedRunner()
    resolver = PathExecutableResolver(overrides={"docker": Path("docker")})
    docker = Docker(runner, resolver)

    endpoint = docker.resolve_current_engine_endpoint()

    assert endpoint == "unix:///Users/dev/.colima/default/docker.sock"
    assert runner.calls[0].argv == ["docker", "context", "show"]
    assert runner.calls[1].argv == [
        "docker",
        "context",
        "inspect",
        "colima",
        "--format",
        "{{.Endpoints.docker.Host}}",
    ]


def test_resolve_current_engine_endpoint_returns_none_on_failure() -> None:
    from olf.deployment.errors import CommandExecutionError

    class FailingRunner(RecordingRunner):
        def run(self, command, **kwargs):  # type: ignore[override]
            raise CommandExecutionError(["docker", "context", "show"], 1, stderr="no docker")

    runner = FailingRunner()
    resolver = PathExecutableResolver(overrides={"docker": Path("docker")})
    docker = Docker(runner, resolver)

    assert docker.resolve_current_engine_endpoint() is None
