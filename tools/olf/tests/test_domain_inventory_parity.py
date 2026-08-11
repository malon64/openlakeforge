"""Tests for libs/domain_inventory.py, the project-code runtime's own
descriptor reader (tools/olf is not installed in the project-code image, so
it cannot import olf.inventory directly — see the module docstring and
ADR 0021).

Loaded by file path rather than a normal import: ``libs`` is a separate
top-level package consumed by the Dagster runtime image, not part of the
``tools/olf`` project this test suite ships with.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from olf.inventory import load_domain_inventory

ROOT = Path(__file__).resolve().parents[3]


def _load_domain_inventory_module() -> ModuleType:
    path = ROOT / "libs" / "domain_inventory.py"
    spec = importlib.util.spec_from_file_location("_libs_domain_inventory_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


domain_inventory = _load_domain_inventory_module()


def test_load_domain_products_matches_the_real_sales_descriptor() -> None:
    products = domain_inventory.load_domain_products(ROOT / "domains", "sales")

    assert [product.id for product in products] == ["order_revenue", "customer_health"]
    order_revenue = products[0]
    assert order_revenue.asset_prefix == "sales_order_revenue"
    assert order_revenue.job_name == "sales_order_revenue_pipeline"
    assert order_revenue.definitions_module == "domains.sales.pipelines.dagster.order_revenue"
    assert order_revenue.bronze_names == ("orders", "order_lines", "products", "channels", "promotions")
    assert order_revenue.gold_names == (
        "mart_order_revenue_by_day",
        "mart_order_revenue_by_channel",
        "mart_order_revenue_margin_by_product",
    )


def test_load_all_products_covers_every_seed_domain() -> None:
    products = domain_inventory.load_all_products(ROOT / "domains")

    assert [product.id for product in products] == ["order_revenue", "customer_health", "inventory_reliability"]


def test_missing_descriptor_names_the_domain(tmp_path: Path) -> None:
    with pytest.raises(domain_inventory.DomainInventoryError, match="domain descriptor not found"):
        domain_inventory.load_domain_products(tmp_path, "missing")


def test_missing_field_names_the_descriptor_product_and_field(tmp_path: Path) -> None:
    path = tmp_path / "sales" / "domain.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(
        """apiVersion: openlakeforge.io/v1alpha1
kind: Domain
name: sales
displayName: Sales
description: Sales domain.
status: planned
data_products:
  - id: orders
    name: sales_orders
    displayName: Sales Orders
    description: Sales orders.
    status: planned
    bronze:
      - name: orders
    silver_tables:
      tables:
        - name: orders
    gold_tables:
      tables:
        - name: mart_orders
""",
        encoding="utf-8",
    )

    with pytest.raises(
        domain_inventory.DomainInventoryError,
        match=r"sales/domain.yaml: product 'orders': asset_prefix is required",
    ):
        domain_inventory.load_domain_products(tmp_path, "sales")


def test_libs_domain_inventory_agrees_with_olf_inventory_on_the_real_repo() -> None:
    """Guard against the two descriptor readers drifting apart (ADR 0021)."""
    olf_inventory = load_domain_inventory(ROOT)
    olf_products = {(product.domain_name, product.id): product for product in olf_inventory.products}

    libs_products = domain_inventory.load_all_products(ROOT / "domains")
    assert len(libs_products) == len(olf_products)

    for libs_product in libs_products:
        olf_product = olf_products[(libs_product.domain_name, libs_product.id)]
        assert libs_product.asset_prefix == olf_product.asset_prefix
        assert libs_product.name == olf_product.name
        assert libs_product.job_name == olf_product.job_name
        assert libs_product.definitions_module == olf_product.definitions_module
        assert libs_product.bronze_names == tuple(table.name for table in olf_product.bronze_tables)
        assert libs_product.silver_names == tuple(table.name for table in olf_product.silver_tables)
        assert libs_product.gold_names == tuple(table.name for table in olf_product.gold_tables)
