"""Injectable executable resolution.

Tool adapters must resolve executables through an `ExecutableResolver`
rather than calling `shutil.which` directly, so a future managed-toolchain
resolver (#127) can be substituted without touching any adapter.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol

from olf.deployment.errors import ExecutableNotFoundError


class ExecutableResolver(Protocol):
    def resolve(self, tool: str) -> Path: ...


class PathExecutableResolver:
    """Resolves tools from PATH, with explicit overrides taking precedence."""

    def __init__(
        self,
        overrides: Mapping[str, Path] | None = None,
        *,
        which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self._overrides = dict(overrides or {})
        self._which = which

    def resolve(self, tool: str) -> Path:
        override = self._overrides.get(tool)
        if override is not None:
            return Path(override)

        found = self._which(tool)
        if found is None:
            raise ExecutableNotFoundError(tool, searched="PATH")
        return Path(found)
