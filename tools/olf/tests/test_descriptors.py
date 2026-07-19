from pathlib import Path

import pytest

from olf.descriptors import DomainDescriptorError, load_domain_descriptor, validate_domain_descriptor

ROOT = Path(__file__).parents[3]


@pytest.mark.parametrize("path", sorted((ROOT / "domains").glob("*/domain.yaml")))
def test_seed_domain_descriptors_are_versioned_and_provider_neutral(path: Path) -> None:
    descriptor = load_domain_descriptor(path)
    assert descriptor["apiVersion"] == "openlakeforge.io/v1alpha1"
    assert descriptor["kind"] == "Domain"


def test_domain_descriptor_rejects_unsupported_version() -> None:
    with pytest.raises(DomainDescriptorError, match="unsupported apiVersion"):
        validate_domain_descriptor({"apiVersion": "openlakeforge.io/v2", "kind": "Domain"})


def test_domain_descriptor_rejects_physical_catalog_identity() -> None:
    descriptor = {
        "apiVersion": "openlakeforge.io/v1alpha1",
        "kind": "Domain",
        "name": "sales",
        "displayName": "Sales",
        "description": "Sales",
        "status": "planned",
        "data_products": [
            {
                "id": "orders",
                "name": "sales_orders",
                "displayName": "Orders",
                "description": "Orders",
                "status": "planned",
                "silver_tables": {"schema": "polaris.lakehouse_dev.sales_orders_silver", "tables": []},
            }
        ],
    }
    with pytest.raises(DomainDescriptorError, match="derived from provider contracts"):
        validate_domain_descriptor(descriptor)


def test_domain_descriptor_rejects_legacy_string_asset() -> None:
    descriptor = {
        "apiVersion": "openlakeforge.io/v1alpha1",
        "kind": "Domain",
        "name": "sales",
        "displayName": "Sales",
        "description": "Sales",
        "status": "planned",
        "data_products": [
            {
                "id": "orders",
                "name": "sales_orders",
                "displayName": "Orders",
                "description": "Orders",
                "status": "planned",
                "assets": ["polaris.lakehouse_dev.sales_orders_gold.orders"],
            }
        ],
    }
    with pytest.raises(DomainDescriptorError, match="logical asset object"):
        validate_domain_descriptor(descriptor)


@pytest.mark.parametrize(
    ("group", "value"),
    [("silver_tables", {}), ("gold_tables", {"tables": ["mart_revenue"]})],
)
def test_domain_descriptor_rejects_malformed_table_groups(group: str, value: object) -> None:
    descriptor = {
        "apiVersion": "openlakeforge.io/v1alpha1",
        "kind": "Domain",
        "name": "sales",
        "displayName": "Sales",
        "description": "Sales",
        "status": "planned",
        "data_products": [
            {
                "id": "orders",
                "name": "sales_orders",
                "displayName": "Orders",
                "description": "Orders",
                "status": "planned",
                group: value,
            }
        ],
    }
    with pytest.raises(DomainDescriptorError):
        validate_domain_descriptor(descriptor)
