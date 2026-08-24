"""AWS provider-contract and storage/catalog preflight checks."""

from __future__ import annotations

from olf import log
from olf.e2e._shell import E2EConfig, E2EError, _run, aws_stack_region, load_provider_contracts_or_raise


def check_aws_provider_contracts(cfg: E2EConfig) -> None:
    log.step("Checking AWS provider contracts...")
    provider_contracts = load_provider_contracts_or_raise(cfg)
    expected = {
        ("storage", "implementation"): "storage.aws_s3",
        ("metadata_database", "implementation"): "metadata_database.aws_rds_postgresql",
        ("catalog", "implementation"): "catalog.aws_glue",
        ("catalog", "catalog_type"): "glue",
        ("artifacts", "implementation"): "artifacts.aws_ecr_and_s3",
    }
    for path, expected_value in expected.items():
        value = provider_contracts
        for key in path:
            value = value[key]
        if value != expected_value:
            raise E2EError(f"provider_contracts.{'.'.join(path)} expected {expected_value!r}, got {value!r}")


def check_aws_storage_and_glue(cfg: E2EConfig) -> None:
    """Assert the S3 artifact bucket and every expected Glue database exist.

    Glue database lifecycle moved to Phase 2 (ADR 0022), so the provider
    contract no longer publishes a namespace/database list to check against --
    `olf catalog sync-namespaces` is what is under test here, and the only
    source of truth for "did it work" is AWS itself.
    """
    log.step("Checking S3 artifact bucket and Glue catalog databases...")
    provider_contracts = load_provider_contracts_or_raise(cfg)
    bucket = provider_contracts["artifact_bucket"]["bucket_name"]
    region = aws_stack_region(cfg)
    expected_schemas = (
        cfg.inventory.bronze_namespace_names
        | cfg.inventory.silver_namespace_names
        | cfg.inventory.gold_namespace_names
    )
    _run(["aws", "s3api", "head-bucket", "--bucket", bucket], capture=True)
    for database in sorted(expected_schemas):
        _run(["aws", "glue", "get-database", "--region", region, "--name", database], capture=True)
