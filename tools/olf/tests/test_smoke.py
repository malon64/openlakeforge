from __future__ import annotations

import signal
import subprocess

import pytest

from olf import smoke


class FakeProcess:
    def __init__(self, *, returncode: int = 0, timeout: bool = False) -> None:
        self.pid = 123
        self.returncode = returncode
        self.timeout = timeout
        self.wait_timeouts: list[float] = []

    def wait(self, timeout: float) -> int:
        self.wait_timeouts.append(timeout)
        if self.timeout and len(self.wait_timeouts) == 1:
            raise subprocess.TimeoutExpired("make", timeout)
        return self.returncode


def test_run_executes_supported_targets_in_order() -> None:
    commands: list[list[str]] = []
    processes = [FakeProcess(), FakeProcess()]

    smoke.run(
        timeout_seconds=2700,
        environ={"CI": "true"},
        monotonic=iter((0.0, 0.0, 10.0, 10.0, 20.0)).__next__,
        popen=lambda command, **kwargs: commands.append(command) or processes.pop(0),
    )

    assert commands == [
        ["make", "local-slim-up"],
        ["make", "local-slim-e2e", "E2E_SUITE=smoke"],
    ]


def test_run_reports_nonzero_make_failure() -> None:
    with pytest.raises(smoke.SmokeError, match="Deploying slim local platform failed with exit code 7"):
        smoke.run(timeout_seconds=2700, popen=lambda *_args, **_kwargs: FakeProcess(returncode=7))


def test_run_kills_process_group_when_a_phase_exceeds_the_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    signals: list[tuple[int, signal.Signals]] = []
    monkeypatch.setattr(smoke.os, "killpg", lambda pid, sig: signals.append((pid, sig)))

    with pytest.raises(smoke.SmokeError, match="2700s time budget"):
        smoke.run(timeout_seconds=2700, popen=lambda *_args, **_kwargs: FakeProcess(timeout=True))

    assert signals == [(123, signal.SIGTERM)]


def test_run_rejects_a_nonpositive_budget() -> None:
    with pytest.raises(smoke.SmokeError, match="greater than zero"):
        smoke.run(timeout_seconds=0)
