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
        "OPENLAKEFORGE_CATALOG_SILVER_SCHEMA_FQNS_JSON": '{"order_revenue": "svc.db.order_revenue_silver"}',
        "OPENLAKEFORGE_CATALOG_GOLD_SCHEMA_FQNS_JSON": "{}",
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
    assert cfg.catalog_database_fqn == "polaris.lakehouse_dev"
    assert cfg.catalog_silver_schema_fqns == {"order_revenue": "svc.db.order_revenue_silver"}


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
