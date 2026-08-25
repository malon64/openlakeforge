from __future__ import annotations

from olf.commands import runtime


def test_standalone_contract_context_honors_custom_contract_root(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    default = tmp_path / "default-contracts"
    override = tmp_path / "custom-contracts"
    monkeypatch.setenv("OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR", str(override))

    assert runtime._contract_terraform_dir(default) == override
