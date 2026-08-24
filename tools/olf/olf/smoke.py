"""Bounded local smoke orchestration without Make or shell subprocesses."""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping

from olf import config, e2e, log
from olf.deployment.context import DeploymentContext, Profile
from olf.deployment.engine import DeploymentEngine, DeploymentPhase, Toolkit, build_provider
from olf.deployment.errors import DeploymentError


class SmokeError(RuntimeError):
    """The bounded smoke deployment failed or exceeded its deadline."""


def run(
    *,
    timeout_seconds: int,
    environ: Mapping[str, str] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    """Deploy slim local, then run its smoke suite under one elapsed-time budget."""
    if timeout_seconds <= 0:
        raise SmokeError("smoke timeout must be greater than zero seconds")
    started = monotonic()
    env = dict(environ or os.environ)
    root = config.repo_root()
    context = DeploymentContext.local(repo_root=root, profile=Profile.SLIM)
    try:
        log.step("Deploying slim local platform...")
        provider = build_provider(context, toolkit=Toolkit.default(), environ=env)
        DeploymentEngine(provider).deploy(DeploymentPhase.ALL)
        _require_remaining(started, timeout_seconds, monotonic, "validating one product pipeline and Gold table")
        log.step("Validating one product pipeline and Gold table...")
        e2e.run("local", suite="smoke", namespace=context.namespace, kube_context=context.kube_context, repo_root=root)
    except DeploymentError as exc:
        raise SmokeError(str(exc)) from exc
    _require_remaining(started, timeout_seconds, monotonic, "completing smoke validation")
    log.info(f"Local slim smoke passed in {int(monotonic() - started)}s (budget: {timeout_seconds}s).")


def _require_remaining(started: float, budget: int, monotonic: Callable[[], float], phase: str) -> None:
    if monotonic() - started >= budget:
        raise SmokeError(f"Local slim smoke exceeded its {budget}s time budget during: {phase}.")
