"""Asserts the Makefile's `local-*`/`azure-*`/`aws-*` targets are pure `olf` delegates.

Item 10 of issue #124 (local) and issue #125 (AWS/Azure) require that Make
contain no Terraform/Docker/kubectl/Helm/deployment-shell orchestration -
every deployment-lifecycle target's recipe must be a single `olf` (or a
`$(MAKE)` re-invocation of another deployment target) delegation.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FORBIDDEN_TOKENS = (
    " terraform",  # leading space: a real invocation ("terraform apply"), not the
    # "infra/terraform/environments/..." contract-dir path e2e targets legitimately reference
    "docker ",
    "kubectl",
    "helm ",
    "bash scripts/local",
    "bash scripts/lib",
    "bash scripts/aws",
    "bash scripts/azure",
    "bash scripts/artifacts/olf.sh",
)
_TARGET_PREFIX_PATTERN = r"^((?:local|azure|aws)-[A-Za-z0-9_-]+):"


def _deployment_target_recipes() -> dict[str, list[str]]:
    makefile_text = (_REPO_ROOT / "Makefile").read_text()
    targets: dict[str, list[str]] = {}
    current: str | None = None
    for line in makefile_text.splitlines():
        match = re.match(_TARGET_PREFIX_PATTERN, line)
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


def test_every_deployment_target_delegates_to_olf_or_another_deployment_target() -> None:
    targets = _deployment_target_recipes()
    assert targets, "expected at least one local-*/azure-*/aws-* Makefile target"
    assert any(name.startswith("azure-") for name in targets)
    assert any(name.startswith("aws-") for name in targets)

    for name, recipe_lines in targets.items():
        joined = "\n".join(recipe_lines)
        assert "OLF_BIN" in joined or "olf " in joined or re.search(r"\$\(MAKE\) (local|azure|aws)-", joined), (
            f"{name}: recipe has no olf/deployment-target delegation:\n{joined}"
        )
        for token in _FORBIDDEN_TOKENS:
            assert token not in joined, f"{name}: recipe still orchestrates via {token!r}:\n{joined}"
