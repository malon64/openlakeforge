"""Project-root selection shared by commands that write user code."""

from __future__ import annotations

from pathlib import Path

from olf.distribution import DistributionError, runtime_layout


def writable_project_root(explicit_root: str) -> Path:
    """Require an explicit writable project for installed distributions."""
    try:
        layout = runtime_layout()
    except DistributionError as exc:
        raise RuntimeError(str(exc)) from exc
    if not explicit_root and not layout.is_source:
        raise RuntimeError("scaffolding cannot modify the bundled demo; pass --repo-root PATH for a writable project")
    return Path(explicit_root).resolve() if explicit_root else layout.project_root
