from __future__ import annotations

from libs.product_dagster import ProductDefinitionSpec, build_product_definitions

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


defs = build_product_definitions(
    ProductDefinitionSpec(
        domain="supply_chain",
        product="inventory_reliability",
        silver_inputs=INVENTORY_RELIABILITY_ENTITIES,
        bronze_inputs=tuple(("erp", entity) for entity in INVENTORY_RELIABILITY_ENTITIES),
        gold_assets=INVENTORY_RELIABILITY_GOLD_ASSETS,
    )
)
