from __future__ import annotations

from pathlib import Path

from libs.bronze_csv import BronzeLoadResult, load_entities_to_bronze

CUSTOMER_HEALTH_ENTITIES = ("accounts", "subscriptions", "support_tickets", "nps_responses")

_DOMAIN_DIR = Path(__file__).resolve().parents[2]
_RAW_DIR = _DOMAIN_DIR / "examples" / "raw" / "customer_health"
_BRONZE_PREFIX = "sales/customer_health"


def load_all_entities_to_bronze(raw_dir: Path | None = None) -> dict[str, BronzeLoadResult]:
    return load_entities_to_bronze(
        entities=CUSTOMER_HEALTH_ENTITIES,
        raw_dir=raw_dir or _RAW_DIR,
        bronze_prefix=_BRONZE_PREFIX,
    )
