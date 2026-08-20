from pathlib import Path

import pytest

from olf import openmetadata as om


def test_storage_bucket_specs_dedup() -> None:
    cfg = om.OpenMetadataConfig.from_environment(
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
    deployer = om.OpenMetadataDeployer(cfg, om.OpenMetadataClient(cfg.base_url))
    names = [spec["name"] for spec in deployer.storage_bucket_specs()]
    assert names == ["lakehouse-bronze", "lakehouse-silver", "lakehouse-gold"]


def test_product_assets_use_provider_schema_fqns_and_dedup() -> None:
    cfg = om.OpenMetadataConfig.from_environment(
        {
            "OPENLAKEFORGE_CATALOG_SILVER_SCHEMA_FQNS_JSON": (
                '{"sales": "aws_glue.lakehouse_dev.sales_silver"}'
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
        "domain": "sales",
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
            "fqn": "aws_glue.lakehouse_dev.sales_silver.order_revenue_silver",
        },
        {
            "type": "table",
            "fqn": "aws_glue.lakehouse_dev.sales_order_revenue_gold.mart_order_revenue",
        },
    ]


def test_logical_asset_name_resolves_through_provider_contract() -> None:
    cfg = om.OpenMetadataConfig.from_environment(
        {
            "OPENLAKEFORGE_CATALOG_SILVER_SCHEMA_FQNS_JSON": "{}",
            "OPENLAKEFORGE_CATALOG_GOLD_SCHEMA_FQNS_JSON":
            '{"sales_order_revenue": "aws_glue.lakehouse_dev.sales_order_revenue_gold"}',
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
        "gold_tables": {"tables": [{"name": "mart_order_revenue"}]},
        "assets": [{"type": "table", "name": "mart_order_revenue"}],
    }

    assert list(deployer.product_asset_entries(product)) == [
        {"type": "table", "fqn": "aws_glue.lakehouse_dev.sales_order_revenue_gold.mart_order_revenue"}
    ]


def test_deployment_input_validation_rejects_new_product_without_contract() -> None:
    cfg = om.OpenMetadataConfig.from_environment(
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
    deployer = om.OpenMetadataDeployer(cfg, om.OpenMetadataClient(cfg.base_url))
    domain_specs = [
        (
            Path("domains/sales/domain.yaml"),
            {
                "name": "sales",
                "data_products": [
                    {
                        "id": "new_product",
                        "name": "sales_new_product",
                        "gold_tables": {"tables": [{"name": "mart_new_product"}]},
                    }
                ],
            },
        )
    ]

    with pytest.raises(om.OpenMetadataError, match="not covered by the provider contract"):
        deployer.validate_deployment_inputs(domain_specs)


def test_deployment_input_validation_rejects_unknown_logical_asset() -> None:
    cfg = om.OpenMetadataConfig.from_environment(
        {
            "OPENLAKEFORGE_CATALOG_SILVER_SCHEMA_FQNS_JSON": "{}",
            "OPENLAKEFORGE_CATALOG_GOLD_SCHEMA_FQNS_JSON": (
                '{"sales_order_revenue": "polaris.lakehouse_dev.sales_order_revenue_gold"}'
            )
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
    deployer = om.OpenMetadataDeployer(cfg, om.OpenMetadataClient(cfg.base_url))
    domain_specs = [
        (
            Path("domains/sales/domain.yaml"),
            {
                "name": "sales",
                "data_products": [
                    {
                        "id": "sales_order_revenue",
                        "name": "sales_order_revenue",
                        "gold_tables": {"tables": [{"name": "mart_order_revenue"}]},
                        "assets": [{"type": "table", "name": "mart_typo"}],
                    }
                ],
            },
        )
    ]

    with pytest.raises(om.OpenMetadataError, match="not declared in the product table contract"):
        deployer.validate_deployment_inputs(domain_specs)


def test_deployment_input_validation_rejects_malformed_bronze_before_writes() -> None:
    cfg = om.OpenMetadataConfig.from_environment(
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
    deployer = om.OpenMetadataDeployer(cfg, om.OpenMetadataClient(cfg.base_url))
    domain_specs = [
        (
            Path("domains/sales/domain.yaml"),
            {
                "name": "sales",
                "data_products": [{"name": "sales_order_revenue", "bronze": ["raw_orders"]}],
            },
        )
    ]

    with pytest.raises(om.OpenMetadataError, match="Bronze entry at index 0 must be an object"):
        deployer.validate_deployment_inputs(domain_specs)


def test_domain_specs_validate_composite_inventory_with_provider_schema_fqns(tmp_path: Path) -> None:
    (tmp_path / "bronze" / "crm").mkdir(parents=True)
    (tmp_path / "bronze" / "crm" / "source.yaml").write_text(
        """apiVersion: openlakeforge.io/v1alpha3
kind: Source
name: crm
displayName: CRM
description: CRM source.
status: active
resources:
  - name: customers
"""
    )
    (tmp_path / "lakehouse.yaml").write_text(
        """apiVersion: openlakeforge.io/v1alpha3
kind: Lakehouse
name: test
displayName: Test
description: Test lakehouse.
status: active
sources: [crm]
domains:
  - name: sales
    displayName: Sales
    description: Sales domain.
    status: active
    silver_tables:
      tables:
        - name: accounts
          source: crm
          resource: accounts
    products:
      - id: customer_health
        displayName: Customer Health
        description: Customer health product.
        status: active
        silver_inputs: [accounts]
        gold_tables:
          tables:
            - name: mart_customer_health
dashboards: []
"""
    )
    cfg = om.OpenMetadataConfig.from_environment(
        {
            "OPENLAKEFORGE_CATALOG_SILVER_SCHEMA_FQNS_JSON": (
                '{"sales": "polaris.lakehouse_dev.sales_silver"}'
            ),
            "OPENLAKEFORGE_CATALOG_GOLD_SCHEMA_FQNS_JSON": (
                '{"customer_health": "polaris.lakehouse_dev.customer_health_gold"}'
            ),
        },
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

    with pytest.raises(
        om.OpenMetadataError,
        match="Silver table 'accounts' references unknown resource 'accounts' of source 'crm'",
    ):
        om.OpenMetadataDeployer(cfg, om.OpenMetadataClient(cfg.base_url))._domain_specs()


def test_deploy_seeds_medallion_buckets_and_bronze_source_hierarchy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "bronze" / "crm").mkdir(parents=True)
    (tmp_path / "bronze" / "crm" / "source.yaml").write_text(
        """apiVersion: openlakeforge.io/v1alpha3
kind: Source
name: crm
displayName: CRM
description: crm source.
status: active
resources:
  - name: orders
    description: Raw sales orders.
"""
    )
    (tmp_path / "lakehouse.yaml").write_text(
        """apiVersion: openlakeforge.io/v1alpha3
kind: Lakehouse
name: test
displayName: Test
description: Test lakehouse.
status: active
sources:
  - crm
domains:
  - name: sales
    displayName: Sales
    description: Sales domain
    status: active
    silver_tables:
      tables:
        - name: raw_orders
          source: crm
          resource: orders
    products:
      - id: order_revenue
        displayName: Sales Order Revenue
        description: Revenue from orders.
        status: active
        silver_inputs: [raw_orders]
        gold_tables:
          tables:
            - name: mart_order_revenue
dashboards: []
"""
    )
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

    def request(method: str, path: str, **_kwargs):
        if method == "GET" and path.startswith("/api/v1/tables/name/"):
            return {
                "id": "table-id",
                "fullyQualifiedName": "polaris.lakehouse_dev.sales_order_revenue_silver.raw_orders",
            }
        return {}

    monkeypatch.setattr(deployer.client, "request", request)
    monkeypatch.setattr(
        deployer,
        "resolve_domain_ref",
        lambda domain_name: {"id": "domain-id", "name": domain_name, "fullyQualifiedName": domain_name},
    )

    def record_container(name, parent_fqn, full_path, description) -> None:
        seeded_containers.append((name, parent_fqn, full_path, description))

    monkeypatch.setattr(deployer, "ensure_container", record_container)

    deployer.deploy()

    assert [(name, parent_fqn, full_path) for name, parent_fqn, full_path, _ in seeded_containers] == [
        ("lakehouse-bronze", None, "s3://lakehouse-bronze"),
        ("lakehouse-silver", None, "s3://lakehouse-silver"),
        ("lakehouse-gold", None, "s3://lakehouse-gold"),
        ("crm", "seaweedfs.lakehouse-bronze", "s3://lakehouse-bronze/crm"),
        (
            "orders",
            "seaweedfs.lakehouse-bronze.crm",
            "s3://lakehouse-bronze/crm/orders",
        ),
    ]
    assert seeded_containers[-1][3] == "Raw sales orders."


def _single_product_deployer(tmp_path: Path) -> om.OpenMetadataDeployer:
    (tmp_path / "bronze" / "crm").mkdir(parents=True)
    (tmp_path / "bronze" / "crm" / "source.yaml").write_text(
        """apiVersion: openlakeforge.io/v1alpha3
kind: Source
name: crm
displayName: CRM
description: crm source.
status: active
resources:
  - name: orders
    description: Raw sales orders.
"""
    )
    (tmp_path / "lakehouse.yaml").write_text(
        """apiVersion: openlakeforge.io/v1alpha3
kind: Lakehouse
name: test
displayName: Test
description: Test lakehouse.
status: active
sources:
  - crm
domains:
  - name: sales
    displayName: Sales
    description: Sales domain
    status: active
    silver_tables:
      tables:
        - name: raw_orders
          source: crm
          resource: orders
        - name: raw_order_lines
          source: crm
          resource: orders
    products:
      - id: order_revenue
        displayName: Sales Order Revenue
        description: Revenue from orders.
        status: active
        silver_inputs: [raw_orders, raw_order_lines]
        gold_tables:
          tables:
            - name: mart_order_revenue
dashboards: []
"""
    )
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
    return om.OpenMetadataDeployer(cfg, om.OpenMetadataClient(cfg.base_url))


def test_deploy_creates_each_schema_before_seeding_its_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deployer = _single_product_deployer(tmp_path)
    ordered: list[str] = []

    monkeypatch.setattr(deployer, "wait_for_openmetadata", lambda: None)
    monkeypatch.setattr(deployer, "login", lambda: None)
    monkeypatch.setattr(deployer, "ensure_storage_service", lambda: None)
    monkeypatch.setattr(deployer, "ensure_container", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        deployer,
        "resolve_domain_ref",
        lambda domain_name: {"id": "d", "name": domain_name, "fullyQualifiedName": domain_name},
    )

    def request(method: str, path: str, **_kwargs):
        if method == "GET" and path.startswith("/api/v1/tables/name/"):
            return {"id": "t", "fullyQualifiedName": "x"}
        return {}

    monkeypatch.setattr(deployer.client, "request", request)
    monkeypatch.setattr(
        deployer, "ensure_database_schema", lambda fqn: ordered.append(f"schema:{fqn}")
    )
    monkeypatch.setattr(
        deployer,
        "ensure_table_stub",
        lambda schema_fqn, name, description: ordered.append(f"table:{schema_fqn}.{name}"),
    )

    deployer.deploy()

    silver = "polaris.lakehouse_dev.sales_silver"
    gold = "polaris.lakehouse_dev.order_revenue_gold"
    assert ordered == [
        f"schema:{silver}",
        f"table:{silver}.raw_orders",
        f"schema:{silver}",
        f"table:{silver}.raw_order_lines",
        f"schema:{gold}",
        f"table:{gold}.mart_order_revenue",
    ]
