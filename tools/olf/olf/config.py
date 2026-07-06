"""Shared runtime settings read from the contract environment."""

from __future__ import annotations

import os


def namespace() -> str:
    return os.environ.get("NAMESPACE") or os.environ.get("OPENLAKEFORGE_KUBE_NAMESPACE") or "lakehouse"


def env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    return default if value is None or value == "" else value
