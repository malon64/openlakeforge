from __future__ import annotations

import pytest

from olf import smoke


def test_run_uses_deployment_engine_and_e2e_without_make(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # noqa: ANN001
    calls: list[object] = []
    monkeypatch.setattr(smoke.config, "repo_root", lambda: tmp_path)
    contexts: list[object] = []
    monkeypatch.setattr(smoke, "build_provider", lambda context, *args, **kwargs: contexts.append(context) or object())
    monkeypatch.setattr(smoke.DeploymentEngine, "deploy", lambda _self, phase: calls.append(phase))
    monkeypatch.setattr(smoke.e2e, "run", lambda *args, **kwargs: calls.append((args, kwargs)))

    smoke.run(
        timeout_seconds=2700,
        environ={"CLUSTER_NAME": "openlakeforge-pr-123", "LOCAL_KUBECONFIG_PATH": str(tmp_path / "ci.yaml")},
        monotonic=iter((0.0, 1.0, 2.0, 3.0)).__next__,
    )

    assert calls[0] is smoke.DeploymentPhase.ALL
    assert calls[1][0] == ("local",)
    assert contexts[0].kube_context == "kind-openlakeforge-pr-123"
    assert calls[1][1]["kube_context"] == "kind-openlakeforge-pr-123"


def test_run_rejects_a_nonpositive_budget() -> None:
    with pytest.raises(smoke.SmokeError, match="greater than zero"):
        smoke.run(timeout_seconds=0)


def test_deadline_interrupts_blocking_work(monkeypatch: pytest.MonkeyPatch) -> None:
    class Signal:
        SIGALRM = 14
        ITIMER_REAL = 0
        handler = None
        timer_calls: list[tuple[object, ...]] = []

        @classmethod
        def getsignal(cls, _signal: int):  # noqa: ANN206
            return "previous"

        @classmethod
        def setitimer(cls, *args: object) -> tuple[int, int]:
            cls.timer_calls.append(args)
            return (0, 0)

        @classmethod
        def signal(cls, _signal: int, handler: object) -> None:
            cls.handler = handler

    monkeypatch.setattr(smoke, "signal", Signal)

    with pytest.raises(smoke.SmokeError, match="time budget"):
        with smoke._deadline(1):
            assert Signal.handler is not None
            Signal.handler(14, None)

    assert Signal.timer_calls[-1] == (0, 0, 0)
