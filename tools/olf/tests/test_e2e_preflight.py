from pathlib import Path

import pytest
from conftest import E2E_INVENTORY, e2e_cfg

from olf.e2e import _preflight, _shell

EXPECTED_GLUE_SCHEMAS = (
    E2E_INVENTORY.bronze_namespace_names | E2E_INVENTORY.silver_namespace_names | E2E_INVENTORY.gold_namespace_names
)


def test_aws_provider_contract_smoke_check(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider_contracts = {
        "storage": {"implementation": "storage.aws_s3"},
        "metadata_database": {"implementation": "metadata_database.aws_rds_postgresql"},
        "catalog": {"implementation": "catalog.aws_glue", "catalog_type": "glue"},
        "artifacts": {"implementation": "artifacts.aws_ecr_and_s3"},
    }
    monkeypatch.setattr(_preflight, "load_provider_contracts_or_raise", lambda _cfg: provider_contracts)

    _preflight.check_aws_provider_contracts(e2e_cfg(tmp_path, env="aws", suite="smoke"))


def test_aws_storage_and_glue_smoke_check_uses_bucket_and_databases(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The contract no longer publishes a namespace/database list (ADR 0022) --
    every expected database name comes straight from the inventory, and the
    check asserts against AWS directly rather than against the contract."""
    provider_contracts = {"artifact_bucket": {"bucket_name": "openlakeforge-ops"}}
    commands: list[list[str]] = []
    monkeypatch.setattr(_preflight, "load_provider_contracts_or_raise", lambda _cfg: provider_contracts)
    # aws_stack_region (called by check_aws_storage_and_glue) calls terraform_output
    # internally within _shell's own module namespace, not _preflight's.
    monkeypatch.setattr(_shell, "terraform_output", lambda _dir, name: "eu-central-1" if name == "aws_region" else "")
    monkeypatch.setattr(_preflight, "_run", lambda args, capture=False: commands.append(args) or "")

    _preflight.check_aws_storage_and_glue(e2e_cfg(tmp_path, env="aws", suite="smoke"))

    assert ["aws", "s3api", "head-bucket", "--bucket", "openlakeforge-ops"] in commands
    glue_commands = [command for command in commands if command[:3] == ["aws", "glue", "get-database"]]
    assert len(glue_commands) == len(EXPECTED_GLUE_SCHEMAS)
    assert all(command[4] == "eu-central-1" for command in glue_commands)


def test_aws_stack_region_prefers_foundation_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setattr(_shell, "terraform_output", lambda _dir, name: "eu-central-1" if name == "aws_region" else "")

    assert _shell.aws_stack_region(e2e_cfg(tmp_path, env="aws", suite="smoke")) == "eu-central-1"
