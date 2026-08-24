from __future__ import annotations

import pytest

from olf import smoke


def test_run_uses_deployment_engine_and_e2e_without_make(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # noqa: ANN001
    calls: list[object] = []
    monkeypatch.setattr(smoke.config, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(smoke, "build_provider", lambda *args, **kwargs: object())
    monkeypatch.setattr(smoke.DeploymentEngine, "deploy", lambda _self, phase: calls.append(phase))
    monkeypatch.setattr(smoke.e2e, "run", lambda *args, **kwargs: calls.append((args, kwargs)))

    smoke.run(timeout_seconds=2700, monotonic=iter((0.0, 1.0, 2.0, 3.0)).__next__)

    assert calls[0] is smoke.DeploymentPhase.ALL
    assert calls[1][0] == ("local",)


def test_run_rejects_a_nonpositive_budget() -> None:
    with pytest.raises(smoke.SmokeError, match="greater than zero"):
        smoke.run(timeout_seconds=0)
