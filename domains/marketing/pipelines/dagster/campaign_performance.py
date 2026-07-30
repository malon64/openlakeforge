from __future__ import annotations

from pathlib import Path

from libs.product_dagster import ProductDefinitionSpec, build_product_definitions

from domains.marketing.extract.dlt.campaign_performance import (
    CAMPAIGN_PERFORMANCE_ENTITIES,
    load_all_entities_to_bronze,
)

_DOMAIN_DIR = Path(__file__).resolve().parents[2]

CAMPAIGN_PERFORMANCE_GOLD_ASSETS = (
    "mart_campaign_spend_by_channel",
    "mart_campaign_conversion_funnel",
    "mart_campaign_return_on_spend",
)

defs = build_product_definitions(
    ProductDefinitionSpec(
        domain="marketing",
        product="campaign_performance",
        asset_prefix="marketing_campaign_performance",
        entities=CAMPAIGN_PERFORMANCE_ENTITIES,
        gold_assets=CAMPAIGN_PERFORMANCE_GOLD_ASSETS,
        domain_dir=_DOMAIN_DIR,
        bronze_loader=load_all_entities_to_bronze,
    )
)
