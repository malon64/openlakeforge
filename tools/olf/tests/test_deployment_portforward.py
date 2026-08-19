from __future__ import annotations

import subprocess
from pathlib import Path

from olf.deployment.portforward import ForwardSpec, ForwardTarget, PortForwardSupervisor
from olf.tooling.kubectl import Kubectl
from olf.tooling.resolver import PathExecutableResolver


class _FakePopen:
    def __init__(self, argv, **kwargs) -> None:  # noqa: ANN001, ARG002
        self.argv = argv
        self.terminated = False
        self.killed = False
        self.waited = False
        self._returncode: int | None = None

    def poll(self):
        return self._returncode

    def terminate(self) -> None:
        self.terminated = True
        self._returncode = 0

    def kill(self) -> None:
        self.killed = True
        self._returncode = -9

    def wait(self, timeout=None):  # noqa: ANN001
        self.waited = True
        return self._returncode


def _kubectl() -> Kubectl:
    from _tooling_support import RecordingRunner

    return Kubectl(RecordingRunner(), PathExecutableResolver(overrides={"kubectl": Path("kubectl")}))


def _spec() -> ForwardSpec:
    return ForwardSpec(
        targets=(
            ForwardTarget("trino", "svc/trino", 8080, 8080),
            ForwardTarget("polaris", "svc/polaris", 8181, 8181),
        ),
        namespace="lakehouse",
        context="kind-openlakeforge-local",
        kubeconfig=Path("/repo/.tmp/kubeconfigs/local.yaml"),
    )


def test_start_builds_argv_via_kubectl_adapter_and_logs_per_target(tmp_path: Path) -> None:
    popens: list[_FakePopen] = []

    def fake_popen(argv, **kwargs):  # noqa: ANN001
        popen = _FakePopen(argv, **kwargs)
        popens.append(popen)
        return popen

    supervisor = PortForwardSupervisor(_kubectl(), log_prefix=tmp_path / "openlakeforge-local", popen=fake_popen)
    spec = _spec()

    supervisor.start(spec.targets[0], spec)

    assert popens[0].argv == [
        "kubectl",
        "--context",
        "kind-openlakeforge-local",
        "port-forward",
        "svc/trino",
        "8080:8080",
        "-n",
        "lakehouse",
    ]
    assert (tmp_path / "openlakeforge-local-trino-port-forward.log").exists()


def test_run_starts_every_target_and_stops_all_on_normal_exit(tmp_path: Path) -> None:
    popens: list[_FakePopen] = []

    def fake_popen(argv, **kwargs):  # noqa: ANN001
        popen = _FakePopen(argv, **kwargs)
        popens.append(popen)
        return popen

    supervisor = PortForwardSupervisor(_kubectl(), log_prefix=tmp_path / "openlakeforge-local", popen=fake_popen)
    spec = _spec()

    supervisor.run(spec, wait=lambda: None)

    assert len(popens) == 2
    assert all(p.terminated for p in popens)


def test_run_stops_all_children_when_the_wait_callable_raises(tmp_path: Path) -> None:
    popens: list[_FakePopen] = []

    def fake_popen(argv, **kwargs):  # noqa: ANN001
        popen = _FakePopen(argv, **kwargs)
        popens.append(popen)
        return popen

    supervisor = PortForwardSupervisor(_kubectl(), log_prefix=tmp_path / "openlakeforge-local", popen=fake_popen)
    spec = _spec()

    def _boom() -> None:
        raise RuntimeError("boom")

    import pytest

    with pytest.raises(RuntimeError):
        supervisor.run(spec, wait=_boom)

    assert all(p.terminated for p in popens)


def test_stop_all_is_idempotent(tmp_path: Path) -> None:
    def fake_popen(argv, **kwargs):  # noqa: ANN001
        return _FakePopen(argv, **kwargs)

    supervisor = PortForwardSupervisor(_kubectl(), log_prefix=tmp_path / "openlakeforge-local", popen=fake_popen)
    spec = _spec()
    supervisor.start(spec.targets[0], spec)

    supervisor.stop_all()
    supervisor.stop_all()  # must not raise on an already-stopped supervisor


def test_stop_all_kills_processes_that_do_not_terminate_in_time(tmp_path: Path) -> None:
    class _StubbornPopen(_FakePopen):
        def wait(self, timeout=None):  # noqa: ANN001
            raise subprocess.TimeoutExpired(cmd="kubectl", timeout=timeout or 0)

    def fake_popen(argv, **kwargs):  # noqa: ANN001
        return _StubbornPopen(argv, **kwargs)

    supervisor = PortForwardSupervisor(_kubectl(), log_prefix=tmp_path / "openlakeforge-local", popen=fake_popen)
    spec = _spec()
    process = supervisor.start(spec.targets[0], spec)

    supervisor.stop_all()

    assert process.killed
