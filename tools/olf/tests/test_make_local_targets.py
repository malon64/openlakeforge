"""Asserts the Makefile's `local-*` targets are pure `olf` delegates.

Item 10 of issue #124 requires that Make contain no Terraform/Docker/
kubectl/Helm deployment orchestration after this change - every `local-*`
recipe line must be a single `olf` (or a `$(MAKE)` re-invocation of another
`local-*` target) delegation.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FORBIDDEN_TOKENS = ("terraform", "docker ", "kubectl", "helm ", "bash scripts/local", "bash scripts/lib")

# local-e2e and local-slim-smoke intentionally keep calling scripts/artifacts/olf.sh
# (an existing thin `uv run olf` wrapper) and `olf smoke run` respectively - #124
# only ports the foundation/platform/artifacts/status/forward/teardown lifecycle,
# not e2e/smoke orchestration.
_EXEMPT_TARGETS = {"local-e2e", "local-slim-smoke"}


def _local_target_recipes() -> dict[str, list[str]]:
    makefile_text = (_REPO_ROOT / "Makefile").read_text()
    targets: dict[str, list[str]] = {}
    current: str | None = None
    for line in makefile_text.splitlines():
        match = re.match(r"^(local-[A-Za-z0-9_-]+):", line)
        if match:
            current = match.group(1)
            targets[current] = []
            continue
        if current is not None:
            if line.startswith("\t"):
                targets[current].append(line)
            elif line.strip() == "" or not line.startswith("\t"):
                current = None
    return targets


def test_every_local_target_delegates_to_olf_or_another_local_target() -> None:
    targets = _local_target_recipes()
    assert targets, "expected at least one local-* Makefile target"

    for name, recipe_lines in targets.items():
        if name in _EXEMPT_TARGETS:
            continue
        joined = "\n".join(recipe_lines)
        assert "OLF_BIN" in joined or "olf " in joined or "$(MAKE) local-" in joined, (
            f"{name}: recipe has no olf/local-target delegation:\n{joined}"
        )
        for token in _FORBIDDEN_TOKENS:
            assert token not in joined, f"{name}: recipe still orchestrates via {token!r}:\n{joined}"
