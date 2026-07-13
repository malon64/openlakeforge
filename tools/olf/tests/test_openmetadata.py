from pathlib import Path

import pytest

from olf import openmetadata as om


def test_display_name_from_name() -> None:
    assert om.display_name_from_name("order_revenue") == "Order Revenue"
    assert om.display_name_from_name("supply-chain") == "Supply Chain"


def test_domain_payload_builds_description_and_defaults() -> None:
    payload = om.domain_payload(
        {
            "name": "sales",
            "description": "Sales domain",
            "status": "active",
            "medallion": {"bronze": {"owner": "ingest", "description": "raw."}},
        }
    )
    assert payload["name"] == "sales"
    assert payload["displayName"] == "Sales"
    assert payload["domainType"] == "Source-aligned"
    assert "Status: active" in payload["description"]
    assert "- bronze: raw. Owner: ingest." in payload["description"]


def test_product_payload_requires_domain() -> None:
    with pytest.raises(om.OpenMetadataError):
        om.product_payload({"name": "order_revenue"})
    payload = om.product_payload({"name": "order_revenue", "domain": "sales"})
    assert payload["domains"] == ["sales"]


def test_product_entries_defaults_name_and_domain() -> None:
    domain = {"name": "sales", "data_products": [{"id": "orders"}]}
    products = list(om.product_entries(domain))
    assert products[0]["name"] == "sales_orders"
    assert products[0]["domain"] == "sales"


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
    cfg = om.OpenMetadataConfig.from_environment(
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


def test_storage_bucket_specs_dedup() -> None:
    cfg = om.OpenMetadataConfig.from_environment(
        {},
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
    deployer = om.OpenMetadataDeployer(cfg, om.OpenMetadataClient(cfg.base_url))
    names = [spec["name"] for spec in deployer.storage_bucket_specs()]
    assert names == ["lakehouse-bronze", "lakehouse-silver", "lakehouse-gold"]


def test_product_assets_use_provider_schema_fqns_and_dedup() -> None:
    cfg = om.OpenMetadataConfig.from_environment(
        {
            "OPENLAKEFORGE_CATALOG_SILVER_SCHEMA_FQNS_JSON": (
                '{"sales_order_revenue": "aws_glue.lakehouse_dev.sales_order_revenue_silver"}'
            ),
            "OPENLAKEFORGE_CATALOG_GOLD_SCHEMA_FQNS_JSON": (
                '{"sales_order_revenue": "aws_glue.lakehouse_dev.sales_order_revenue_gold"}'
            ),
        },
        base_url="http://x",
        admin_email="a",
        admin_password="p",
        metadata_root="domains",
        metadata_source_dir="",
        allow_missing_assets=False,
        catalog_service="aws_glue",
        catalog_database="lakehouse_dev",
        cleanup_legacy_default_database=False,
    )
    deployer = om.OpenMetadataDeployer(cfg, om.OpenMetadataClient(cfg.base_url))
    product = {
        "name": "sales_order_revenue",
        "silver_tables": {
            "schema": "polaris.lakehouse_dev.sales_order_revenue_silver",
            "tables": [{"name": "order_revenue_silver"}],
        },
        "gold_tables": {
            "schema": "polaris.lakehouse_dev.sales_order_revenue_gold",
            "tables": [{"name": "mart_order_revenue"}],
        },
        "assets": [
            "polaris.lakehouse_dev.sales_order_revenue_silver.order_revenue_silver",
            {
                "type": "table",
                "fullyQualifiedName": "polaris.lakehouse_dev.sales_order_revenue_gold.mart_order_revenue",
            },
        ],
    }

    assets = list(deployer.product_asset_entries(product))

    assert assets == [
        {
            "type": "table",
            "fqn": "aws_glue.lakehouse_dev.sales_order_revenue_silver.order_revenue_silver",
        },
        {
            "type": "table",
            "fqn": "aws_glue.lakehouse_dev.sales_order_revenue_gold.mart_order_revenue",
        },
    ]


def test_deploy_seeds_medallion_buckets_at_storage_service_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "sales").mkdir()
    (tmp_path / "sales" / "domain.yaml").write_text("name: sales\ndata_products: []\n")
    cfg = om.OpenMetadataConfig.from_environment(
        {},
        base_url="http://x",
        admin_email="a",
        admin_password="p",
        metadata_root=str(tmp_path),
        metadata_source_dir="",
        allow_missing_assets=False,
        catalog_service="polaris",
        catalog_database="lakehouse_dev",
        cleanup_legacy_default_database=False,
    )
    deployer = om.OpenMetadataDeployer(cfg, om.OpenMetadataClient(cfg.base_url))
    seeded_containers = []

    monkeypatch.setattr(deployer, "wait_for_openmetadata", lambda: None)
    monkeypatch.setattr(deployer, "login", lambda: None)
    monkeypatch.setattr(deployer, "ensure_storage_service", lambda: None)
    monkeypatch.setattr(deployer.client, "request", lambda *args, **kwargs: {})

    def record_container(name, parent_fqn, full_path, description) -> None:
        seeded_containers.append((name, parent_fqn, full_path, description))

    monkeypatch.setattr(deployer, "ensure_container", record_container)

    deployer.deploy()

    assert [(name, parent_fqn, full_path) for name, parent_fqn, full_path, _ in seeded_containers] == [
        ("lakehouse-bronze", None, "s3://lakehouse-bronze"),
        ("lakehouse-silver", None, "s3://lakehouse-silver"),
        ("lakehouse-gold", None, "s3://lakehouse-gold"),
    ]
