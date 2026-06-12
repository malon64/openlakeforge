from __future__ import annotations

from pathlib import Path

from libs.bronze_csv import BronzeLoadResult, load_entities_to_bronze

INVENTORY_RELIABILITY_ENTITIES = (
    "warehouses",
    "suppliers",
    "inventory_snapshots",
    "purchase_orders",
    "shipments",
    "stockout_events",
)

_DOMAIN_DIR = Path(__file__).resolve().parents[2]
_RAW_DIR = _DOMAIN_DIR / "examples" / "raw" / "inventory_reliability"
_BRONZE_PREFIX = "bronze/supply_chain/inventory_reliability"


def load_all_entities_to_bronze(raw_dir: Path | None = None) -> dict[str, BronzeLoadResult]:
    return load_entities_to_bronze(
        entities=INVENTORY_RELIABILITY_ENTITIES,
        raw_dir=raw_dir or _RAW_DIR,
        bronze_prefix=_BRONZE_PREFIX,
    )
