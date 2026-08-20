from __future__ import annotations

from libs.product_dagster import ProductDefinitionSpec, build_product_definitions

from lakehouse_code.bronze.erp.dlt.erp import BronzeLoadResult, load_erp_entities_to_bronze

INVENTORY_RELIABILITY_ENTITIES = (
    "warehouses",
    "suppliers",
    "inventory_snapshots",
    "purchase_orders",
    "shipments",
    "stockout_events",
)

INVENTORY_RELIABILITY_GOLD_ASSETS = (
    "mart_inventory_position",
    "mart_supplier_delivery_reliability",
    "mart_stockout_risk",
)


def _load_inventory_reliability_bronze() -> dict[str, BronzeLoadResult]:
    return load_erp_entities_to_bronze(INVENTORY_RELIABILITY_ENTITIES)


defs = build_product_definitions(
    ProductDefinitionSpec(
        domain="supply_chain",
        product="inventory_reliability",
        entities=INVENTORY_RELIABILITY_ENTITIES,
        gold_assets=INVENTORY_RELIABILITY_GOLD_ASSETS,
        bronze_loader=_load_inventory_reliability_bronze,
    )
)