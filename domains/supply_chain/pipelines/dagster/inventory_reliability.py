from __future__ import annotations

from pathlib import Path

from libs.product_dagster import ProductDefinitionSpec, build_product_definitions

from domains.supply_chain.extract.dlt.inventory_reliability import (
    INVENTORY_RELIABILITY_ENTITIES,
    load_all_entities_to_bronze,
)

_DOMAIN_DIR = Path(__file__).resolve().parents[2]

INVENTORY_RELIABILITY_GOLD_ASSETS = (
    "mart_inventory_position",
    "mart_supplier_delivery_reliability",
    "mart_stockout_risk",
)

defs = build_product_definitions(
    ProductDefinitionSpec(
        domain="supply_chain",
        product="inventory_reliability",
        asset_prefix="supply_chain_inventory_reliability",
        entities=INVENTORY_RELIABILITY_ENTITIES,
        gold_assets=INVENTORY_RELIABILITY_GOLD_ASSETS,
        domain_dir=_DOMAIN_DIR,
        bronze_loader=load_all_entities_to_bronze,
    )
)
