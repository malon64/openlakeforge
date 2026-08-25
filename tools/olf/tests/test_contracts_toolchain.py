"""`load_provider_contracts`'s managed-toolchain resolution behaviour (#127).

Split from `test_contracts.py`, which covers `build_contract_env`/
`render_shell_exports` against fixture contracts and never touches
executable resolution.
"""

from __future__ import annotations

import pytest

from olf.contracts import load_provider_contracts
from olf.deployment.errors import ExecutableNotFoundError, ToolchainError


def test_missing_terraform_executable_is_treated_as_not_applied_yet(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resolver:
        def resolve(self, tool: str):  # noqa: ANN001, ANN202
            raise ExecutableNotFoundError(tool, searched="PATH")

    monkeypatch.setattr("olf.tooling.resolver.build_resolver", lambda: _Resolver())

    assert load_provider_contracts("some/terraform/dir") is None


def test_a_real_toolchain_failure_propagates_instead_of_being_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A digest mismatch, broken download, or malformed catalog is a real
    operational failure, not an "unapplied" signal - a caller that treated
    it as unapplied would silently fall back to defaults (e.g. enabling
    governance/analytics for what should be a slim deployment) instead of
    failing closed."""

    class _Resolver:
        def resolve(self, tool: str):  # noqa: ANN001, ANN202
            raise ToolchainError(tool, reason="digest mismatch")

    monkeypatch.setattr("olf.tooling.resolver.build_resolver", lambda: _Resolver())

    with pytest.raises(ToolchainError):
        load_provider_contracts("some/terraform/dir")
