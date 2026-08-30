from __future__ import annotations

import os
from pathlib import Path

import pytest

from olf import contracts as contracts_module
from olf.deployment import contract_env


def _kwargs(tmp_path: Path, **overrides: object) -> dict[str, object]:
    base = {
        "contract_terraform_dir": tmp_path / "infra/terraform/environments/aws-poc",
        "repo_root": tmp_path,
        "namespace": "lakehouse",
        "kube_context": "eks-openlakeforge-poc",
        "kubeconfig_path": tmp_path / ".tmp/kubeconfigs/aws.yaml",
        "port_forward_log_prefix": Path("/tmp/openlakeforge-aws"),
    }
    base.update(overrides)
    return base


def test_applies_contract_exports_and_restores_previous_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(contracts_module, "load_provider_contracts", lambda terraform_dir: None)
    monkeypatch.setattr(
        contracts_module,
        "build_contract_env",
        lambda base, contracts_value, *, repo_root, **_: (
            {"OPENLAKEFORGE_CATALOG_NAME": "lakehouse_dev"},
            ["SOME_STALE_VAR"],
        ),
    )
    monkeypatch.setenv("SOME_STALE_VAR", "old-value")
    monkeypatch.delenv("OPENLAKEFORGE_CATALOG_NAME", raising=False)

    with contract_env.applied_contract_environment(**_kwargs(tmp_path)) as env:
        assert os.environ["OPENLAKEFORGE_CATALOG_NAME"] == "lakehouse_dev"
        assert "SOME_STALE_VAR" not in os.environ
        assert os.environ["NAMESPACE"] == "lakehouse"
        assert os.environ["KUBE_CONTEXT"] == "eks-openlakeforge-poc"
        assert env["NAMESPACE"] == "lakehouse"

    assert "OPENLAKEFORGE_CATALOG_NAME" not in os.environ
    assert os.environ["SOME_STALE_VAR"] == "old-value"


def test_extra_variables_override_contract_exports(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(contracts_module, "load_provider_contracts", lambda terraform_dir: None)
    monkeypatch.setattr(
        contracts_module,
        "build_contract_env",
        lambda base, contracts_value, *, repo_root, **_: ({"NAMESPACE": "from-contract"}, []),
    )

    with contract_env.applied_contract_environment(**_kwargs(tmp_path, namespace="lakehouse-slim")):
        assert os.environ["NAMESPACE"] == "lakehouse-slim"


def test_restores_environment_even_when_the_block_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(contracts_module, "load_provider_contracts", lambda terraform_dir: None)
    monkeypatch.setattr(
        contracts_module,
        "build_contract_env",
        lambda base, contracts_value, *, repo_root, **_: ({"OPENLAKEFORGE_CATALOG_NAME": "lakehouse_dev"}, []),
    )
    monkeypatch.delenv("OPENLAKEFORGE_CATALOG_NAME", raising=False)

    with pytest.raises(RuntimeError):
        with contract_env.applied_contract_environment(**_kwargs(tmp_path)):
            raise RuntimeError("boom")

    assert "OPENLAKEFORGE_CATALOG_NAME" not in os.environ


def test_uses_the_scoped_environment_for_contract_terraform(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    observed: dict[str, str] = {}

    def _load(terraform_dir: str, *, environ: dict[str, str]) -> None:
        observed.update(environ)
        assert terraform_dir == str(tmp_path / "infra/terraform/environments/aws-poc")
        return None

    monkeypatch.setattr(contracts_module, "load_provider_contracts", _load)
    monkeypatch.setattr(contracts_module, "build_contract_env", lambda *args, **kwargs: ({}, []))

    with contract_env.applied_contract_environment(
        **_kwargs(tmp_path),
        environ={
            "OLF_DISTRIBUTION_ROOT": str(tmp_path / "payload"),
            "OPENLAKEFORGE_TERRAFORM_STATE_ROOT": str(tmp_path / "state/aws"),
            "OPENLAKEFORGE_TERRAFORM_DATA_ROOT": str(tmp_path / "work/aws/terraform-data"),
        },
    ) as env:
        assert env["OLF_DISTRIBUTION_ROOT"] == str(tmp_path / "payload")

    assert observed["OLF_DISTRIBUTION_ROOT"] == str(tmp_path / "payload")
    assert observed["OPENLAKEFORGE_TERRAFORM_STATE_ROOT"] == str(tmp_path / "state/aws")
