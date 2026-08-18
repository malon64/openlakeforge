from pathlib import Path

from olf import openmetadata as om
from olf.openmetadata._config import OpenMetadataConfig


def test_config_from_environment_reads_schema_fqns() -> None:
    environ = {
        "OPENLAKEFORGE_CATALOG_DATABASE_FQN": "aws_glue.lakehouse_dev",
        "OPENLAKEFORGE_CATALOG_SILVER_SCHEMA_FQNS_JSON": '{"order_revenue": "svc.db.order_revenue_silver"}',
        "OPENLAKEFORGE_CATALOG_GOLD_SCHEMA_FQNS_JSON": "{}",
        "OPENLAKEFORGE_STORAGE_OM_SERVICE": "aws_s3",
        "OPENLAKEFORGE_STORAGE_DISPLAY_NAME": "AWS S3",
        "OPENLAKEFORGE_STORAGE_ENDPOINT": "",
        "OPENLAKEFORGE_STORAGE_REGION": "eu-west-1",
        "OPENLAKEFORGE_STORAGE_BRONZE_BUCKET": "openlakeforge-poc-bronze",
        "OPENLAKEFORGE_STORAGE_SILVER_BUCKET": "openlakeforge-poc-silver",
        "OPENLAKEFORGE_STORAGE_GOLD_BUCKET": "openlakeforge-poc-gold",
    }
    cfg = OpenMetadataConfig.from_environment(
        environ,
        base_url="http://127.0.0.1:18585/",
        admin_email="admin@open-metadata.org",
        admin_password="admin",
        metadata_root="domains",
        metadata_source_dir="",
        allow_missing_assets=True,
        catalog_service="polaris",
        catalog_database="lakehouse_dev",
        cleanup_legacy_default_database=True,
    )
    assert cfg.base_url == "http://127.0.0.1:18585"
    assert cfg.catalog_database_fqn == "aws_glue.lakehouse_dev"
    assert cfg.catalog_silver_schema_fqns == {"order_revenue": "svc.db.order_revenue_silver"}
    assert cfg.storage_service == "aws_s3"
    assert cfg.storage_display_name == "AWS S3"
    assert cfg.storage_endpoint == ""
    assert cfg.storage_region == "eu-west-1"
    assert cfg.storage_bronze_bucket == "openlakeforge-poc-bronze"
    assert cfg.storage_silver_bucket == "openlakeforge-poc-silver"
    assert cfg.storage_gold_bucket == "openlakeforge-poc-gold"


def test_config_from_environment_defaults_seed_schema_fqns_for_direct_cli() -> None:
    cfg = OpenMetadataConfig.from_environment(
        {},
        base_url="http://x",
        admin_email="a",
        admin_password="p",
        metadata_root="domains",
        metadata_source_dir="",
        allow_missing_assets=False,
        catalog_service="",
        catalog_database="",
        cleanup_legacy_default_database=False,
    )

    assert cfg.catalog_service == "polaris"
    assert cfg.catalog_database == "lakehouse_dev"
    assert cfg.catalog_silver_schema_fqns["sales_order_revenue"] == (
        "polaris.lakehouse_dev.sales_order_revenue_silver"
    )
    assert cfg.catalog_gold_schema_fqns["sales_order_revenue"] == (
        "polaris.lakehouse_dev.sales_order_revenue_gold"
    )


def test_config_from_environment_derives_defaults_from_metadata_source_dir_override(tmp_path: Path) -> None:
    """metadata_source_dir must drive the FQN defaults, matching what domain_files() actually deploys.

    metadata_root here does not even exist, so the defaults could only have
    come from the override.
    """
    source_dir = tmp_path / "override"
    source_dir.mkdir()
    (source_dir / "domain.yaml").write_text(
        """apiVersion: openlakeforge.io/v1alpha2
kind: Domain
name: override
displayName: Override
description: Override-only domain.
status: active
data_products:
  - id: widgets
    name: override_widgets
    displayName: Override Widgets
    description: Widgets.
    status: active
    asset_prefix: override_widgets
    bronze:
      - name: source
        path: s3://lakehouse-bronze/override/widgets/source
    silver_tables:
      tables:
        - name: source
    gold_tables:
      tables:
        - name: mart_widgets
"""
    )

    cfg = OpenMetadataConfig.from_environment(
        {},
        base_url="http://x",
        admin_email="a",
        admin_password="p",
        metadata_root=str(tmp_path / "does-not-exist"),
        metadata_source_dir=str(source_dir),
        allow_missing_assets=False,
        catalog_service="polaris",
        catalog_database="lakehouse_dev",
        cleanup_legacy_default_database=False,
    )

    assert cfg.catalog_silver_schema_fqns == {"override_widgets": "polaris.lakehouse_dev.override_widgets_silver"}
    assert cfg.catalog_gold_schema_fqns == {"override_widgets": "polaris.lakehouse_dev.override_widgets_gold"}


def test_config_from_environment_accepts_a_standalone_metadata_file_override(tmp_path: Path) -> None:
    """A single-file override's parent directory name must not have to match the domain.

    Mirrors a mounted or temporary path like /metadata/domain.yaml, where the
    parent directory carries no significance — only the file's own `name:`
    field identifies the domain.
    """
    metadata_dir = tmp_path / "metadata"
    metadata_dir.mkdir()
    descriptor_path = metadata_dir / "domain.yaml"
    descriptor_path.write_text(
        """apiVersion: openlakeforge.io/v1alpha2
kind: Domain
name: sales
displayName: Sales
description: Standalone sales descriptor.
status: active
data_products:
  - id: orders
    name: sales_orders
    displayName: Sales Orders
    description: Orders.
    status: active
    asset_prefix: sales_orders
    bronze:
      - name: source
        path: s3://lakehouse-bronze/sales/orders/source
    silver_tables:
      tables:
        - name: source
    gold_tables:
      tables:
        - name: mart_orders
"""
    )

    cfg = OpenMetadataConfig.from_environment(
        {},
        base_url="http://x",
        admin_email="a",
        admin_password="p",
        metadata_root=str(tmp_path / "does-not-exist"),
        metadata_source_dir=str(descriptor_path),
        allow_missing_assets=False,
        catalog_service="polaris",
        catalog_database="lakehouse_dev",
        cleanup_legacy_default_database=False,
    )

    assert cfg.catalog_silver_schema_fqns == {"sales_orders": "polaris.lakehouse_dev.sales_orders_silver"}
    deployer = om.OpenMetadataDeployer(cfg, om.OpenMetadataClient(cfg.base_url))
    assert deployer.domain_files() == [descriptor_path]


def test_config_from_environment_preserves_explicit_empty_schema_contract() -> None:
    cfg = OpenMetadataConfig.from_environment(
        {
            "OPENLAKEFORGE_CATALOG_SILVER_SCHEMA_FQNS_JSON": "{}",
            "OPENLAKEFORGE_CATALOG_GOLD_SCHEMA_FQNS_JSON": "{}",
        },
        base_url="http://x",
        admin_email="a",
        admin_password="p",
        metadata_root="domains",
        metadata_source_dir="",
        allow_missing_assets=False,
        catalog_service="polaris",
        catalog_database="lakehouse_dev",
        cleanup_legacy_default_database=False,
    )

    assert cfg.catalog_silver_schema_fqns == {}
    assert cfg.catalog_gold_schema_fqns == {}
