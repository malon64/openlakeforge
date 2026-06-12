from __future__ import annotations

from pathlib import Path

from libs.product_dagster import ProductDefinitionSpec, build_product_definitions

from domains.sales.extract.dlt.customer_health import (
    CUSTOMER_HEALTH_ENTITIES,
    load_all_entities_to_bronze,
)

_DOMAIN_DIR = Path(__file__).resolve().parents[2]

CUSTOMER_HEALTH_GOLD_ASSETS = (
    "mart_customer_health_score",
    "mart_churn_risk_by_segment",
    "mart_support_sla_by_customer",
)

defs = build_product_definitions(
    ProductDefinitionSpec(
        domain="sales",
        product="customer_health",
        asset_prefix="sales_customer_health",
        entities=CUSTOMER_HEALTH_ENTITIES,
        gold_assets=CUSTOMER_HEALTH_GOLD_ASSETS,
        domain_dir=_DOMAIN_DIR,
        bronze_loader=load_all_entities_to_bronze,
    )
)
