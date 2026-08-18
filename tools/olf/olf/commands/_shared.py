"""Shared CLI helpers for command groups."""

from __future__ import annotations

from olf import log


def log_step(message: str) -> None:
    """Announce a CLI step."""
    log.step(message)


def fail(message: str) -> int:
    """Log error and return exit code 1."""
    log.error(message)
    return 1
