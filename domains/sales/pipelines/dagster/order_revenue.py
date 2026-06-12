from __future__ import annotations

from pathlib import Path

from libs.product_dagster import ProductDefinitionSpec, build_product_definitions

from domains.sales.extract.dlt.order_revenue import (
    ORDER_REVENUE_ENTITIES,
    load_all_entities_to_bronze,
)

_DOMAIN_DIR = Path(__file__).resolve().parents[2]

ORDER_REVENUE_GOLD_ASSETS = (
    "mart_order_revenue_by_day",
    "mart_order_revenue_by_channel",
    "mart_order_revenue_margin_by_product",
)

defs = build_product_definitions(
    ProductDefinitionSpec(
        domain="sales",
        product="order_revenue",
        asset_prefix="sales_order_revenue",
        entities=ORDER_REVENUE_ENTITIES,
        gold_assets=ORDER_REVENUE_GOLD_ASSETS,
        domain_dir=_DOMAIN_DIR,
        bronze_loader=load_all_entities_to_bronze,
    )
)
