from __future__ import annotations

from pathlib import Path

from libs.bronze_csv import BronzeLoadResult, load_entities_to_bronze

ORDER_REVENUE_ENTITIES = ("orders", "order_lines", "products", "channels", "promotions")

_DOMAIN_DIR = Path(__file__).resolve().parents[2]
_RAW_DIR = _DOMAIN_DIR / "examples" / "raw" / "order_revenue"
_BRONZE_PREFIX = "sales/order_revenue"


def load_all_entities_to_bronze(raw_dir: Path | None = None) -> dict[str, BronzeLoadResult]:
    return load_entities_to_bronze(
        entities=ORDER_REVENUE_ENTITIES,
        raw_dir=raw_dir or _RAW_DIR,
        bronze_prefix=_BRONZE_PREFIX,
    )
