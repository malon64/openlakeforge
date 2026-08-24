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


def test_pull_with_platform_inserts_platform_flag_before_image() -> None:
    docker, runner = _docker()

    docker.pull("python:3.12-slim", platform="linux/amd64")

    assert runner.last_call.argv == ["docker", "pull", "--platform", "linux/amd64", "python:3.12-slim"]


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


def test_server_arch_returns_stripped_output() -> None:
    docker, _runner = _docker(CommandResult(argv=(), returncode=0, stdout="arm64\n", stderr="", duration_seconds=0.0))

    assert docker.server_arch() == "arm64"


def test_server_arch_returns_empty_string_on_failure() -> None:
    from olf.deployment.errors import CommandExecutionError

    class FailingRunner(RecordingRunner):
        def run(self, command, **kwargs):  # type: ignore[override]
            raise CommandExecutionError(["docker", "version"], 1, stderr="daemon not running")

    docker = Docker(FailingRunner(), PathExecutableResolver(overrides={"docker": Path("docker")}))

    assert docker.server_arch() == ""


def test_image_exists_reflects_inspect_exit_code() -> None:
    result = CommandResult(argv=(), returncode=1, stdout="", stderr="no such image", duration_seconds=0.0)
    docker, _runner = _docker(result)

    assert docker.image_exists("apache/polaris:1.4.0") is False


def test_image_inspect_defaults_to_check_false() -> None:
    docker, runner = _docker()

    docker.image_inspect("apache/polaris:1.4.0")

    assert runner.last_call.kwargs["check"] is False
    assert runner.last_call.argv == ["docker", "image", "inspect", "apache/polaris:1.4.0"]


def test_save_builds_exact_argv() -> None:
    docker, runner = _docker()

    docker.save("apache/polaris:1.4.0", output_path=Path("/tmp/work/polaris.tar"))

    assert runner.last_call.argv == ["docker", "save", "apache/polaris:1.4.0", "-o", "/tmp/work/polaris.tar"]


def test_exec_builds_privileged_interactive_argv_with_stdin_path() -> None:
    docker, runner = _docker()

    docker.exec_(
        "openlakeforge-local-control-plane",
        ["ctr", "--namespace=k8s.io", "images", "import", "--snapshotter=overlayfs", "-"],
        privileged=True,
        interactive=True,
        stdin_path=Path("/tmp/work/polaris.tar"),
    )

    assert runner.last_call.argv == [
        "docker",
        "exec",
        "--privileged",
        "-i",
        "openlakeforge-local-control-plane",
        "ctr",
        "--namespace=k8s.io",
        "images",
        "import",
        "--snapshotter=overlayfs",
        "-",
    ]
    assert runner.last_call.kwargs["stdin_path"] == Path("/tmp/work/polaris.tar")


def test_exec_without_privileged_or_interactive() -> None:
    docker, runner = _docker()

    docker.exec_("node", ["crictl", "inspecti", "apache/polaris:1.4.0"])

    assert runner.last_call.argv == ["docker", "exec", "node", "crictl", "inspecti", "apache/polaris:1.4.0"]


def test_run_container_builds_expected_argv() -> None:
    docker, runner = _docker()

    docker.run_container(
        "ghcr.io/malon64/floe:0.6.11",
        [
            "validate",
            "-c",
            "/work/domains/orders/contracts/floe/orders.yml",
            "-p",
            "/work/libs/floe/profiles/local-k8s.yml",
        ],
        platform="linux/amd64",
        env_names=["AWS_REGION"],
        volumes=["/repo:/work"],
        workdir="/",
    )

    assert runner.last_call.argv == [
        "docker",
        "run",
        "--rm",
        "--platform",
        "linux/amd64",
        "-e",
        "AWS_REGION",
        "-v",
        "/repo:/work",
        "-w",
        "/",
        "ghcr.io/malon64/floe:0.6.11",
        "validate",
        "-c",
        "/work/domains/orders/contracts/floe/orders.yml",
        "-p",
        "/work/libs/floe/profiles/local-k8s.yml",
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


def test_resolve_current_engine_endpoint_returns_none_when_docker_is_not_installed() -> None:
    from olf.deployment.errors import ExecutableNotFoundError

    class MissingDockerRunner(RecordingRunner):
        def run(self, command, **kwargs):  # type: ignore[override]
            raise ExecutableNotFoundError("docker")

    runner = MissingDockerRunner()
    resolver = PathExecutableResolver(overrides={"docker": Path("docker")})
    docker = Docker(runner, resolver)

    assert docker.resolve_current_engine_endpoint() is None
