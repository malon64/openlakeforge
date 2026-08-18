"""Shared runtime settings read from the contract environment."""

from __future__ import annotations

import os
from pathlib import Path


def namespace() -> str:
    return os.environ.get("NAMESPACE") or os.environ.get("OPENLAKEFORGE_KUBE_NAMESPACE") or "lakehouse"


def env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    return default if value is None or value == "" else value


def repo_root() -> Path:
    return Path(os.environ.get("OPENLAKEFORGE_REPO_ROOT", ".")).resolve()


def truthy(value: str) -> bool:
    return value.lower() in {"1", "true", "yes", "y"}
