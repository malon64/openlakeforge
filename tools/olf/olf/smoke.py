"""Bounded local deployment smoke orchestration."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass

from olf import log


class SmokeError(RuntimeError):
    """The bounded smoke deployment failed or exceeded its deadline."""


@dataclass(frozen=True)
class SmokePhase:
    """One supported Make target in the local smoke path."""

    label: str
    target: str


PHASES = (
    SmokePhase("Deploying slim local platform", "local-slim-up"),
    SmokePhase("Validating one product pipeline and Gold table", "local-slim-e2e E2E_SUITE=smoke"),
)


def run(
    *,
    timeout_seconds: int,
    environ: Mapping[str, str] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    popen: Callable[..., subprocess.Popen[str]] = subprocess.Popen,
) -> None:
    """Run the local slim smoke target sequence within one hard deadline."""
    if timeout_seconds <= 0:
        raise SmokeError("smoke timeout must be greater than zero seconds")

    started = monotonic()
    deadline = started + timeout_seconds
    env = dict(environ or os.environ)
    for phase in PHASES:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise SmokeError(_timeout_message(timeout_seconds, phase.label))
        log.step(f"{phase.label} ({int(remaining)}s remaining)...")
        _run_make_phase(
            phase,
            env=env,
            timeout_seconds=remaining,
            budget_seconds=timeout_seconds,
            popen=popen,
        )
    log.info(f"Local slim smoke passed in {int(monotonic() - started)}s (budget: {timeout_seconds}s).")


def _run_make_phase(
    phase: SmokePhase,
    *,
    env: Mapping[str, str],
    timeout_seconds: float,
    budget_seconds: int,
    popen: Callable[..., subprocess.Popen[str]],
) -> None:
    command = ["make", *phase.target.split()]
    process = popen(command, env=dict(env), start_new_session=True, text=True)
    try:
        returncode = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        _terminate_process_group(process)
        raise SmokeError(_timeout_message(budget_seconds, phase.label)) from exc
    if returncode != 0:
        raise SmokeError(f"{phase.label} failed with exit code {returncode}.")


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    """Terminate Make and every child it started before returning control to CI."""
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=10)


def _timeout_message(timeout_seconds: int, phase: str) -> str:
    return f"Local slim smoke exceeded its {timeout_seconds}s time budget during: {phase}."
