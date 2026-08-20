from __future__ import annotations

from libs.product_dagster import ProductDefinitionSpec, build_product_definitions

from lakehouse_code.bronze.crm.dlt.crm import BronzeLoadResult, load_crm_entities_to_bronze

CUSTOMER_HEALTH_ENTITIES = ("accounts", "subscriptions", "support_tickets", "nps_responses")

CUSTOMER_HEALTH_GOLD_ASSETS = (
    "mart_customer_health_score",
    "mart_churn_risk_by_segment",
    "mart_support_sla_by_customer",
)


def _load_customer_health_bronze() -> dict[str, BronzeLoadResult]:
    return load_crm_entities_to_bronze(CUSTOMER_HEALTH_ENTITIES)


defs = build_product_definitions(
    ProductDefinitionSpec(
        domain="sales",
        product="customer_health",
        entities=CUSTOMER_HEALTH_ENTITIES,
        gold_assets=CUSTOMER_HEALTH_GOLD_ASSETS,
        bronze_loader=_load_customer_health_bronze,
    )
)