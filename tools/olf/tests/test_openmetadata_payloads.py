import pytest

from olf.clients.openmetadata import OpenMetadataError
from olf.openmetadata import _payloads


def test_display_name_from_name() -> None:
    assert _payloads.display_name_from_name("order_revenue") == "Order Revenue"
    assert _payloads.display_name_from_name("supply-chain") == "Supply Chain"


def test_domain_payload_builds_description_and_defaults() -> None:
    payload = _payloads.domain_payload(
        {
            "name": "sales",
            "description": "Sales domain",
            "status": "active",
            "medallion": {"bronze": {"owner": "ingest", "description": "raw."}},
        }
    )
    assert payload["name"] == "sales"
    assert payload["displayName"] == "Sales"
    assert payload["domainType"] == "Source-aligned"
    assert "Status: active" in payload["description"]
    assert "- bronze: raw. Owner: ingest." in payload["description"]


def test_product_payload_requires_domain() -> None:
    with pytest.raises(OpenMetadataError):
        _payloads.product_payload({"name": "order_revenue"})
    payload = _payloads.product_payload({"name": "order_revenue", "domain": "sales"})
    assert payload["domains"] == ["sales"]


def test_product_entries_defaults_name_and_domain() -> None:
    domain = {"name": "sales", "data_products": [{"id": "orders"}]}
    products = list(_payloads.product_entries(domain))
    assert products[0]["name"] == "sales_orders"
    assert products[0]["domain"] == "sales"
