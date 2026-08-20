from __future__ import annotations

from pathlib import Path

from _tooling_support import RecordingRunner

from olf.deployment.charts import ChartRequest, prepare_cached_chart
from olf.deployment.context import DeploymentContext
from olf.deployment.retry import RetryPolicy
from olf.tooling.helm import Helm
from olf.tooling.process import CommandResult
from olf.tooling.resolver import PathExecutableResolver

_POLICY = RetryPolicy(max_attempts=2, delay_seconds=0)


def _request(tmp_path: Path) -> ChartRequest:
    return ChartRequest(
        display_name="Trino",
        repo_name="trino",
        repo_url="https://trinodb.github.io/charts",
        chart_ref="trino/trino",
        version="1.42.2",
        package_path=tmp_path / "helm" / "charts" / "trino-1.42.2.tgz",
    )


def test_reuses_valid_cached_chart(tmp_path: Path) -> None:
    request = _request(tmp_path)
    request.package_path.parent.mkdir(parents=True)
    request.package_path.write_text("cached")
    runner = RecordingRunner(CommandResult(argv=(), returncode=0, stdout="", stderr="", duration_seconds=0.0))
    helm = Helm(runner, PathExecutableResolver(overrides={"helm": Path("helm")}))
    context = DeploymentContext.local(repo_root=tmp_path)

    result = prepare_cached_chart(request, helm=helm, paths=context.paths, env={}, retry_policy=_POLICY)

    assert result == request.package_path
    assert len(runner.calls) == 1  # only the `helm show chart` cache-validity check
    assert runner.calls[0].argv[1:3] == ["show", "chart"]


def test_downloads_when_no_cached_chart_exists(tmp_path: Path) -> None:
    request = _request(tmp_path)
    runner = RecordingRunner(CommandResult(argv=(), returncode=0, stdout="", stderr="", duration_seconds=0.0))
    helm = Helm(runner, PathExecutableResolver(overrides={"helm": Path("helm")}))
    context = DeploymentContext.local(repo_root=tmp_path)

    prepare_cached_chart(request, helm=helm, paths=context.paths, env={}, retry_policy=_POLICY)

    subcommands = [tuple(c.argv[1:3]) for c in runner.calls]
    assert ("repo", "add") in subcommands
    assert ("repo", "update") in subcommands
    assert ("pull", "trino/trino") in subcommands
    add_call = next(c for c in runner.calls if c.argv[1:3] == ["repo", "add"])
    assert add_call.kwargs["retry_policy"] is _POLICY


def test_redownloads_when_cache_is_invalid(tmp_path: Path) -> None:
    request = _request(tmp_path)
    request.package_path.parent.mkdir(parents=True)
    request.package_path.write_text("corrupt")

    class FailingShowRunner(RecordingRunner):
        def run(self, command, **kwargs):  # type: ignore[override]
            from _tooling_support import RecordedCall

            argv = list(command.argv) if hasattr(command, "argv") else [str(p) for p in command]
            self.calls.append(RecordedCall(argv=argv, kwargs=kwargs))
            if argv[1:3] == ["show", "chart"]:
                return CommandResult(argv=(), returncode=1, stdout="", stderr="bad chart", duration_seconds=0.0)
            return CommandResult(argv=(), returncode=0, stdout="", stderr="", duration_seconds=0.0)

    runner = FailingShowRunner()
    helm = Helm(runner, PathExecutableResolver(overrides={"helm": Path("helm")}))
    context = DeploymentContext.local(repo_root=tmp_path)

    prepare_cached_chart(request, helm=helm, paths=context.paths, env={}, retry_policy=_POLICY)

    assert not request.package_path.exists()  # the corrupt package was removed before re-download
    assert any(c.argv[1:3] == ["pull", "trino/trino"] for c in runner.calls)
