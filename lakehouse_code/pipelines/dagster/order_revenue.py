from __future__ import annotations

from libs.product_dagster import ProductDefinitionSpec, build_product_definitions

from lakehouse_code.bronze.crm.dlt.crm import BronzeLoadResult, load_crm_entities_to_bronze

ORDER_REVENUE_ENTITIES = ("orders", "order_lines", "products", "channels", "promotions", "accounts")

ORDER_REVENUE_GOLD_ASSETS = (
    "mart_order_revenue_by_day",
    "mart_order_revenue_by_channel",
    "mart_order_revenue_margin_by_product",
    "mart_order_revenue_by_account_segment",
)

# "accounts" is also loaded by pipelines/dagster/customer_health.py: both products
# consume the same crm.accounts Bronze resource into the shared sales_silver.accounts
# table (domain-owned Silver). The two Dagster jobs still materialize it under
# separate product-scoped asset keys (see ADR 0025) rather than one shared asset both
# depend on -- each run just re-writes the same table idempotently.


def _load_order_revenue_bronze() -> dict[str, BronzeLoadResult]:
    return load_crm_entities_to_bronze(ORDER_REVENUE_ENTITIES)


defs = build_product_definitions(
    ProductDefinitionSpec(
        domain="sales",
        product="order_revenue",
        entities=ORDER_REVENUE_ENTITIES,
        gold_assets=ORDER_REVENUE_GOLD_ASSETS,
        bronze_loader=_load_order_revenue_bronze,
    )
)