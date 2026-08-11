from __future__ import annotations

import json
from pathlib import Path

import pytest

from olf.descriptors import DomainDescriptorError
from olf.inventory import (
    external_result,
    inventory_for,
    load_domain_inventory,
    load_domain_inventory_from_descriptors,
)

ROOT = Path(__file__).parents[3]


def _descriptor(domain: str, product_id: str = "orders", asset_prefix: str | None = None) -> str:
    prefix = asset_prefix or f"{domain}_{product_id}"
    return f"""\
apiVersion: openlakeforge.io/v1alpha2
kind: Domain
name: {domain}
displayName: {domain.title()}
description: {domain} descriptor.
status: planned
data_products:
  - id: {product_id}
    name: {prefix}
    displayName: {product_id.replace('_', ' ').title()}
    description: {product_id} product.
    status: planned
    asset_prefix: {prefix}
    bronze:
      - name: source
        path: s3://lakehouse-bronze/{domain}/{product_id}/source
    silver_tables:
      tables:
        - name: source
    gold_tables:
      tables:
        - name: mart_{product_id}
"""


def _write_descriptor(root: Path, domain: str, content: str) -> Path:
    path = root / "domains" / domain / "domain.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_seed_inventory_derives_every_product_expectation() -> None:
    inventory = load_domain_inventory(ROOT)

    assert [domain.name for domain in inventory.domains] == ["sales", "supply_chain"]
    assert [product.asset_prefix for product in inventory.products] == [
        "sales_order_revenue",
        "sales_customer_health",
        "supply_chain_inventory_reliability",
    ]
    assert [product.job_name for product in inventory.products] == [
        "sales_order_revenue_pipeline",
        "sales_customer_health_pipeline",
        "supply_chain_inventory_reliability_pipeline",
    ]
    assert inventory.catalog_namespaces == (
        "sales_order_revenue_silver",
        "sales_order_revenue_gold",
        "sales_customer_health_silver",
        "sales_customer_health_gold",
        "supply_chain_inventory_reliability_silver",
        "supply_chain_inventory_reliability_gold",
    )
    assert [table.name for table in inventory.products[0].silver_tables] == [
        "orders",
        "order_lines",
        "products",
        "channels",
        "promotions",
    ]
    assert [table.name for table in inventory.products[0].gold_tables] == [
        "mart_order_revenue_by_day",
        "mart_order_revenue_by_channel",
        "mart_order_revenue_margin_by_product",
    ]
    assert inventory.expected_dashboards == {
        "sales-order-revenue": "Sales Order Revenue",
        "sales-customer-health": "Sales Customer Health",
        "supply-chain-inventory-reliability": "Supply Chain Inventory Reliability",
    }
    assert inventory.products[0].openmetadata_data_product_fqns == (
        "sales_order_revenue",
        "sales.sales_order_revenue",
    )
    assert inventory.products[0].artifact_prefixes.manifest_key == (
        "floe/manifests/sales/order_revenue/order_revenue.manifest.json"
    )
    assert inventory.domain_names == ("sales", "supply_chain")
    assert inventory.silver_table_count == 15
    assert inventory.gold_table_count == 9
    assert inventory.gold_mart_names == (
        "sales_order_revenue_gold.mart_order_revenue_by_day",
        "sales_order_revenue_gold.mart_order_revenue_by_channel",
        "sales_order_revenue_gold.mart_order_revenue_margin_by_product",
        "sales_customer_health_gold.mart_customer_health_score",
        "sales_customer_health_gold.mart_churn_risk_by_segment",
        "sales_customer_health_gold.mart_support_sla_by_customer",
        "supply_chain_inventory_reliability_gold.mart_inventory_position",
        "supply_chain_inventory_reliability_gold.mart_supplier_delivery_reliability",
        "supply_chain_inventory_reliability_gold.mart_stockout_risk",
    )
    assert inventory.manifest_keys == (
        "floe/manifests/sales/order_revenue/order_revenue.manifest.json",
        "floe/manifests/sales/customer_health/customer_health.manifest.json",
        "floe/manifests/supply_chain/inventory_reliability/inventory_reliability.manifest.json",
    )
    assert inventory.openmetadata_data_products == {
        "sales_order_revenue": ("sales_order_revenue", "sales.sales_order_revenue"),
        "sales_customer_health": ("sales_customer_health", "sales.sales_customer_health"),
        "supply_chain_inventory_reliability": (
            "supply_chain_inventory_reliability",
            "supply_chain.supply_chain_inventory_reliability",
        ),
    }
    assert inventory.silver_namespace_names == frozenset(
        {"sales_order_revenue_silver", "sales_customer_health_silver", "supply_chain_inventory_reliability_silver"}
    )
    assert inventory.gold_namespace_names == frozenset(
        {"sales_order_revenue_gold", "sales_customer_health_gold", "supply_chain_inventory_reliability_gold"}
    )
    assert inventory.products[0].superset_export_bundle_name == "sales_order_revenue_superset_assets_export.zip"


def test_inventory_for_caches_by_resolved_repo_root() -> None:
    assert inventory_for(ROOT) is inventory_for(ROOT)
    assert inventory_for(ROOT) is inventory_for(str(ROOT))


def test_load_domain_inventory_from_descriptors_matches_the_directory_loader(tmp_path: Path) -> None:
    """An explicit-paths load must produce the same inventory a directory glob would."""
    path = _write_descriptor(tmp_path, "sales", _descriptor("sales"))

    by_directory = load_domain_inventory(tmp_path)
    by_paths = load_domain_inventory_from_descriptors([path], source_label=path)

    assert by_paths.products[0].asset_prefix == by_directory.products[0].asset_prefix
    assert by_paths.products[0].job_name == by_directory.products[0].job_name


def test_load_domain_inventory_from_descriptors_rejects_an_empty_set() -> None:
    with pytest.raises(DomainDescriptorError, match="no domain descriptors found"):
        load_domain_inventory_from_descriptors([], source_label="nowhere")


def test_inventory_resolves_provider_physical_names() -> None:
    inventory = load_domain_inventory(ROOT)
    names = inventory.resolve_physical_names(
        catalog_database_fqn="aws_glue.lakehouse_dev",
        silver_bucket="openlakeforge-poc-silver",
        gold_bucket="openlakeforge-poc-gold",
        manifest_base_uri="s3://openlakeforge-poc-ops/floe/manifests",
    )

    assert names.catalog_namespaces[0].name == "sales_order_revenue_silver"
    assert names.catalog_namespaces[0].location == "s3://openlakeforge-poc-silver/sales_order_revenue_silver/"
    assert names.silver_schema_fqns["sales_order_revenue"] == "aws_glue.lakehouse_dev.sales_order_revenue_silver"
    assert names.gold_schema_fqns["supply_chain_inventory_reliability"] == (
        "aws_glue.lakehouse_dev.supply_chain_inventory_reliability_gold"
    )
    assert names.products[0].manifest_uri == (
        "s3://openlakeforge-poc-ops/floe/manifests/sales/order_revenue/order_revenue.manifest.json"
    )


def test_inventory_reports_descriptor_product_and_missing_field(tmp_path: Path) -> None:
    path = _write_descriptor(tmp_path, "sales", _descriptor("sales").replace("    asset_prefix: sales_orders\n", ""))

    with pytest.raises(
        DomainDescriptorError,
        match=r"sales/domain.yaml: product 'orders': missing required field 'asset_prefix'",
    ):
        load_domain_inventory(tmp_path)

    assert path.is_file()


def test_inventory_rejects_duplicate_asset_prefixes(tmp_path: Path) -> None:
    _write_descriptor(tmp_path, "sales", _descriptor("sales", asset_prefix="shared_product"))
    duplicate = _descriptor("supply_chain", asset_prefix="shared_product")
    duplicate = duplicate.replace("name: shared_product", "name: supply_chain_orders", 1)
    _write_descriptor(tmp_path, "supply_chain", duplicate)

    with pytest.raises(DomainDescriptorError, match=r"duplicate asset_prefix 'shared_product'"):
        load_domain_inventory(tmp_path)


def test_inventory_rejects_duplicate_product_ids_within_a_domain(tmp_path: Path) -> None:
    duplicate = _descriptor("sales") + """\
  - id: orders
    name: sales_orders_copy
    displayName: Orders copy
    description: Orders copy product.
    status: planned
    asset_prefix: sales_orders_copy
    bronze:
      - name: source_copy
        path: s3://lakehouse-bronze/sales/orders/source_copy
    silver_tables:
      tables:
        - name: source_copy
    gold_tables:
      tables:
        - name: mart_orders_copy
"""
    _write_descriptor(tmp_path, "sales", duplicate)

    with pytest.raises(DomainDescriptorError, match=r"duplicate id within domain 'sales'"):
        load_domain_inventory(tmp_path)


def test_inventory_requires_descriptor_name_to_match_its_directory(tmp_path: Path) -> None:
    _write_descriptor(tmp_path, "retail", _descriptor("sales"))

    with pytest.raises(DomainDescriptorError, match=r"name 'sales' must match descriptor directory 'retail'"):
        load_domain_inventory(tmp_path)


def test_inventory_requires_v1alpha2_with_migration_guidance(tmp_path: Path) -> None:
    legacy = _descriptor("sales").replace("openlakeforge.io/v1alpha2", "openlakeforge.io/v1alpha1")
    _write_descriptor(tmp_path, "sales", legacy)

    with pytest.raises(DomainDescriptorError, match=r"domain-v1alpha1-to-v1alpha2"):
        load_domain_inventory(tmp_path)


def test_renaming_descriptor_product_changes_discovered_work_without_shared_code(tmp_path: Path) -> None:
    descriptor = _descriptor("sales", product_id="revenue", asset_prefix="sales_revenue")
    _write_descriptor(tmp_path, "sales", descriptor)

    inventory = load_domain_inventory(tmp_path)
    product = inventory.default_product

    assert product.job_name == "sales_revenue_pipeline"
    assert product.silver_namespace == "sales_revenue_silver"
    assert product.dashboard.slug == "sales-revenue"
    assert product.openmetadata_data_product_fqns == ("sales_revenue", "sales.sales_revenue")
    assert product.artifact_prefixes.manifest_key == "floe/manifests/sales/revenue/revenue.manifest.json"
    assert product.definitions_module == "domains.sales.pipelines.dagster.revenue"
    assert inventory.job_names == ("sales_revenue_pipeline",)
    assert inventory.gold_mart_names == ("sales_revenue_gold.mart_revenue",)
    assert inventory.manifest_keys == ("floe/manifests/sales/revenue/revenue.manifest.json",)
    assert inventory.silver_namespace_names == frozenset({"sales_revenue_silver"})


def test_inventory_external_result_is_terraform_compatible() -> None:
    result = external_result(ROOT)
    payload = json.loads(result["inventory"])

    assert payload["aggregate_definitions_module"] == "domains.definitions"
    assert payload["domains"] == [
        {
            "code_location_name": "sales-dagster",
            "definitions_module": "domains.sales.definitions",
            "name": "sales",
        },
        {
            "code_location_name": "supply-chain-dagster",
            "definitions_module": "domains.supply_chain.definitions",
            "name": "supply_chain",
        },
    ]
    assert payload["products"][0] == {
        "asset_prefix": "sales_order_revenue",
        "domain_name": "sales",
        "gold_namespace": "sales_order_revenue_gold",
        "id": "order_revenue",
        "manifest_key": "floe/manifests/sales/order_revenue/order_revenue.manifest.json",
        "silver_namespace": "sales_order_revenue_silver",
    }
