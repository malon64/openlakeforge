from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from olf.commands import diagnostics
from olf.deployment.errors import ExecutableNotFoundError


def test_collect_keeps_kubernetes_diagnostics_when_docker_is_missing(
    monkeypatch, tmp_path: Path
) -> None:  # noqa: ANN001
    calls: list[list[str]] = []

    class Resolver:
        def resolve(self, name: str) -> Path:
            if name == "docker":
                raise ExecutableNotFoundError(name)
            assert name == "kubectl"
            return Path("kubectl")

    class Runner:
        def run(self, argv, **kwargs):  # noqa: ANN001, ANN003
            calls.append(argv)
            return SimpleNamespace(stdout="", stderr="")

    monkeypatch.setattr(diagnostics.Toolkit, "default", lambda: SimpleNamespace(resolver=Resolver(), runner=Runner()))

    diagnostics.collect(tmp_path, namespace="lakehouse")

    assert "ExecutableNotFoundError" in (tmp_path / "docker-disk.txt").read_text()
    assert (tmp_path / "all-events.txt").is_file()
    assert any(call[0] == "kubectl" for call in calls)
