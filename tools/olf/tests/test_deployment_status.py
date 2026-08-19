from __future__ import annotations

from pathlib import Path

from _tooling_support import RecordingRunner

from olf.deployment.status import collect_status
from olf.tooling.kubectl import Kubectl
from olf.tooling.process import CommandResult
from olf.tooling.resolver import PathExecutableResolver


def test_collect_status_queries_pods_services_and_pvcs_in_order() -> None:
    runner = RecordingRunner(CommandResult(argv=(), returncode=0, stdout="ok\n", stderr="", duration_seconds=0.0))
    kubectl = Kubectl(runner, PathExecutableResolver(overrides={"kubectl": Path("kubectl")}))

    report = collect_status(
        kubectl,
        namespace="lakehouse",
        context="kind-openlakeforge-local",
        kubeconfig=Path("/repo/.tmp/kubeconfigs/local.yaml"),
    )

    resources = [call.argv[call.argv.index("get") + 1] for call in runner.calls]
    assert resources == ["pods", "svc", "pvc"]
    assert [section.title for section in report.sections] == ["Pods", "Services", "PVCs"]
    assert all(call.kwargs["check"] is False for call in runner.calls)


def test_render_joins_sections_with_headers() -> None:
    result = CommandResult(argv=(), returncode=0, stdout="", stderr="no resources", duration_seconds=0.0)
    runner = RecordingRunner(result)
    kubectl = Kubectl(runner, PathExecutableResolver(overrides={"kubectl": Path("kubectl")}))

    report = collect_status(
        kubectl,
        namespace="lakehouse",
        context="kind-openlakeforge-local",
        kubeconfig=Path("/repo/.tmp/kubeconfigs/local.yaml"),
    )

    rendered = report.render()
    assert "=== Pods ===" in rendered
    assert "=== Services ===" in rendered
    assert "=== PVCs ===" in rendered
