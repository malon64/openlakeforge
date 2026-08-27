from __future__ import annotations

from pathlib import Path

import pytest
from openlakeforge_domain import DomainDescriptorError, load_domain_descriptor, validate_domain_descriptor

ROOT = Path(__file__).parents[3]


def _product(**overrides: object) -> dict[str, object]:
    product: dict[str, object] = {
        "id": "orders",
        "name": "sales_orders",
        "displayName": "Orders",
        "description": "Orders",
        "status": "planned",
        "asset_prefix": "sales_orders",
        "bronze": [{"name": "orders", "path": "s3://lakehouse-bronze/sales/orders"}],
        "silver_tables": {"tables": [{"name": "orders"}]},
        "gold_tables": {"tables": [{"name": "mart_orders"}]},
    }
    product.update(overrides)
    return product


def _descriptor(product: dict[str, object]) -> dict[str, object]:
    return {
        "apiVersion": "openlakeforge.io/v1alpha2",
        "kind": "Domain",
        "name": "sales",
        "displayName": "Sales",
        "description": "Sales",
        "status": "planned",
        "data_products": [product],
    }


@pytest.mark.parametrize("path", sorted((ROOT / "domains").glob("*/domain.yaml")))
def test_seed_domain_descriptors_are_versioned_and_provider_neutral(path: Path) -> None:
    descriptor = load_domain_descriptor(path)
    assert descriptor["apiVersion"] == "openlakeforge.io/v1alpha2"
    assert descriptor["kind"] == "Domain"


def test_domain_descriptor_rejects_unsupported_version() -> None:
    with pytest.raises(DomainDescriptorError, match="unsupported apiVersion"):
        validate_domain_descriptor({"apiVersion": "openlakeforge.io/v2", "kind": "Domain"})


def test_v1alpha1_preserves_its_optional_inventory_fields() -> None:
    descriptor = _descriptor(_product())
    descriptor["apiVersion"] = "openlakeforge.io/v1alpha1"
    product = descriptor["data_products"][0]
    assert isinstance(product, dict)
    for field in ("asset_prefix", "bronze", "silver_tables", "gold_tables"):
        product.pop(field)

    validate_domain_descriptor(descriptor)


def test_domain_descriptor_rejects_physical_catalog_identity() -> None:
    with pytest.raises(DomainDescriptorError, match="derived from provider contracts"):
        validate_domain_descriptor(
            _descriptor(
                _product(
                    silver_tables={
                        "schema": "polaris.lakehouse_dev.sales_orders_silver",
                        "tables": [{"name": "orders"}],
                    }
                )
            )
        )


def test_domain_descriptor_rejects_legacy_string_asset() -> None:
    with pytest.raises(DomainDescriptorError, match="logical asset object"):
        validate_domain_descriptor(_descriptor(_product(assets=["polaris.lakehouse_dev.sales_orders_gold.orders"])))


def test_domain_descriptor_rejects_logical_asset_without_name() -> None:
    with pytest.raises(DomainDescriptorError, match="logical name"):
        validate_domain_descriptor(_descriptor(_product(assets=[{"type": "table"}])))


@pytest.mark.parametrize("field", ["fqn", "fullyQualifiedName"])
def test_domain_descriptor_rejects_physical_table_identity(field: str) -> None:
    with pytest.raises(DomainDescriptorError, match="physical FQNs"):
        validate_domain_descriptor(
            _descriptor(
                _product(
                    gold_tables={"tables": [{"name": "mart_orders", field: "polaris.lakehouse.mart_orders"}]}
                )
            )
        )


def test_domain_descriptor_rejects_non_string_table_name() -> None:
    with pytest.raises(DomainDescriptorError, match="non-empty string name"):
        validate_domain_descriptor(_descriptor(_product(gold_tables={"tables": [{"name": 123}]})))


def test_domain_descriptor_rejects_malformed_bronze_entry() -> None:
    with pytest.raises(DomainDescriptorError, match=r"bronze\[0\] must be an object"):
        validate_domain_descriptor(_descriptor(_product(bronze=["raw_orders"])))


def test_domain_descriptor_rejects_unsupported_logical_asset_type() -> None:
    with pytest.raises(DomainDescriptorError, match="type 'table'"):
        validate_domain_descriptor(_descriptor(_product(assets=[{"type": "dashboard", "name": "mart_orders"}])))


@pytest.mark.parametrize(
    ("group", "value"),
    [("silver_tables", {}), ("gold_tables", {"tables": ["mart_revenue"]})],
)
def test_domain_descriptor_rejects_malformed_table_groups(group: str, value: object) -> None:
    with pytest.raises(DomainDescriptorError):
        validate_domain_descriptor(_descriptor(_product(**{group: value})))


def test_domain_descriptor_requires_inventory_fields_with_product_context() -> None:
    product = _product()
    product.pop("asset_prefix")

    with pytest.raises(DomainDescriptorError, match=r"product 'orders': missing required field 'asset_prefix'"):
        validate_domain_descriptor(_descriptor(product), source="domains/sales/domain.yaml")
