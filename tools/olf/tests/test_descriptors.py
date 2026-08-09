import textwrap
from pathlib import Path

import pytest

from olf.descriptors import (
    DomainDescriptorError,
    discover_domains,
    discover_products,
    load_domain_descriptor,
    validate_domain_descriptor,
)

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


def test_domain_descriptor_rejects_logical_asset_without_name() -> None:
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
                "assets": [{"type": "table"}],
            }
        ],
    }
    with pytest.raises(DomainDescriptorError, match="logical name"):
        validate_domain_descriptor(descriptor)


@pytest.mark.parametrize("field", ["fqn", "fullyQualifiedName"])
def test_domain_descriptor_rejects_physical_table_identity(field: str) -> None:
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
                "gold_tables": {"tables": [{"name": "mart_orders", field: "polaris.lakehouse.mart_orders"}]},
            }
        ],
    }
    with pytest.raises(DomainDescriptorError, match="physical FQNs"):
        validate_domain_descriptor(descriptor)


def test_domain_descriptor_rejects_non_string_table_name() -> None:
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
                "gold_tables": {"tables": [{"name": 123}]},
            }
        ],
    }
    with pytest.raises(DomainDescriptorError, match="non-empty string name"):
        validate_domain_descriptor(descriptor)


def test_domain_descriptor_rejects_malformed_bronze_entry() -> None:
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
                "bronze": ["raw_orders"],
            }
        ],
    }
    with pytest.raises(DomainDescriptorError, match="bronze\\[0\\] must be an object"):
        validate_domain_descriptor(descriptor)


def test_domain_descriptor_rejects_unsupported_logical_asset_type() -> None:
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
                "assets": [{"type": "dashboard", "name": "mart_orders"}],
            }
        ],
    }
    with pytest.raises(DomainDescriptorError, match="type 'table'"):
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


# --- Discovery -------------------------------------------------------------


def _write_domain(root: Path, domain: str, document: str) -> None:
    domain_dir = root / "domains" / domain
    domain_dir.mkdir(parents=True, exist_ok=True)
    (domain_dir / "domain.yaml").write_text(textwrap.dedent(document), encoding="utf-8")


_WIDGETS_DOMAIN_YAML = """\
    apiVersion: openlakeforge.io/v1alpha1
    kind: Domain
    name: widgets
    displayName: Widgets
    description: Widgets domain fixture.
    status: planned
    data_products:
      - id: catalog
        name: widgets_catalog
        displayName: Widgets Catalog
        description: Widgets catalog data product.
        status: planned
        silver_tables:
          tables:
            - name: items
        gold_tables:
          tables:
            - name: mart_items_by_category
    """


def test_discover_domains_returns_empty_tuple_for_missing_domains_dir(tmp_path: Path) -> None:
    assert discover_domains(tmp_path) == ()
    assert discover_products(tmp_path) == ()


def test_discover_domains_finds_seed_domains() -> None:
    domains = discover_domains(ROOT)
    # marketing is onboarded purely through `olf product scaffold`, so finding it
    # here is the proof that discovery needs no per-product platform change.
    assert {domain.name for domain in domains} == {"sales", "supply_chain", "marketing"}
    sales = next(domain for domain in domains if domain.name == "sales")
    assert {product.id for product in sales.products} == {"order_revenue", "customer_health"}
    marketing = next(domain for domain in domains if domain.name == "marketing")
    assert {product.id for product in marketing.products} == {"campaign_performance"}


def test_discover_products_derives_physical_names_from_the_logical_descriptor() -> None:
    products = {product.asset_prefix: product for product in discover_products(ROOT)}
    order_revenue = products["sales_order_revenue"]
    assert order_revenue.domain == "sales"
    assert order_revenue.id == "order_revenue"
    assert order_revenue.job_name == "sales_order_revenue_pipeline"
    assert order_revenue.silver_namespace == "sales_order_revenue_silver"
    assert order_revenue.gold_namespace == "sales_order_revenue_gold"
    assert order_revenue.manifest_key == "floe/manifests/sales/order_revenue/order_revenue.manifest.json"
    assert order_revenue.dashboard_slug == "sales-order-revenue"
    assert order_revenue.dashboard_title == "Sales Order Revenue"
    assert "orders" in order_revenue.silver_tables
    assert "mart_order_revenue_by_day" in order_revenue.gold_tables


def test_discover_products_picks_up_a_new_product_with_no_code_edit(tmp_path: Path) -> None:
    """Prove onboarding is automatic: adding a fixture domain.yaml changes discovery output alone."""
    before = discover_products(tmp_path)
    assert before == ()

    _write_domain(tmp_path, "widgets", _WIDGETS_DOMAIN_YAML)

    after = {product.asset_prefix: product for product in discover_products(tmp_path)}
    assert set(after) == {"widgets_catalog"}
    widget = after["widgets_catalog"]
    assert widget.job_name == "widgets_catalog_pipeline"
    assert widget.silver_namespace == "widgets_catalog_silver"
    assert widget.gold_namespace == "widgets_catalog_gold"
    assert widget.manifest_key == "floe/manifests/widgets/catalog/catalog.manifest.json"
    assert widget.gold_tables == ("mart_items_by_category",)

    domain_names = {domain.name for domain in discover_domains(tmp_path)}
    assert domain_names == {"widgets"}


def test_discover_products_asset_prefix_defaults_to_domain_and_product_id(tmp_path: Path) -> None:
    """When `asset_prefix` is omitted, it is derived from `<domain>_<product id>`."""
    document = """\
    apiVersion: openlakeforge.io/v1alpha1
    kind: Domain
    name: marketing
    displayName: Marketing
    description: Marketing domain fixture.
    status: planned
    data_products:
      - id: campaign_performance
        name: marketing_campaign_performance
        displayName: Marketing Campaign Performance
        description: Campaign performance data product.
        status: planned
    """
    _write_domain(tmp_path, "marketing", document)

    (product,) = discover_products(tmp_path)
    assert product.asset_prefix == "marketing_campaign_performance"


def test_domain_descriptor_name_must_match_its_directory(tmp_path: Path) -> None:
    """A syntactically valid `name` that disagrees with the directory it
    lives in (domains/foo/domain.yaml declaring `name: bar`) is discovered
    as domain "bar" while the actual product code stays under domains/foo:
    the structure check then searches domains/bar, and Terraform deploys
    domains.bar.definitions, neither of which the on-disk layout matches.
    """
    document = """\
    apiVersion: openlakeforge.io/v1alpha1
    kind: Domain
    name: bar
    displayName: Bar
    description: Mismatched domain fixture.
    status: planned
    data_products: []
    """
    _write_domain(tmp_path, "foo", document)

    with pytest.raises(DomainDescriptorError, match="must match its directory name 'foo'"):
        load_domain_descriptor(tmp_path / "domains" / "foo" / "domain.yaml")


def test_discover_products_rejects_duplicate_asset_prefix_across_domains(tmp_path: Path) -> None:
    """asset_prefix is used as an object key throughout (Terraform's
    product_floe_manifest_relative_paths/catalog_product_namespaces outputs,
    contracts.py's fallback schema-FQN maps). Two products declaring the
    same explicit asset_prefix, even from different domain files, produce a
    duplicate Terraform object key that fails evaluation with no reference
    to which descriptors caused it.
    """
    shared_prefix_a = """\
    apiVersion: openlakeforge.io/v1alpha1
    kind: Domain
    name: alpha
    displayName: Alpha
    description: Alpha domain fixture.
    status: planned
    data_products:
      - id: widgets
        name: alpha_widgets
        asset_prefix: shared_prefix
        displayName: Alpha Widgets
        description: Fixture product.
        status: planned
    """
    shared_prefix_b = """\
    apiVersion: openlakeforge.io/v1alpha1
    kind: Domain
    name: beta
    displayName: Beta
    description: Beta domain fixture.
    status: planned
    data_products:
      - id: widgets
        name: beta_widgets
        asset_prefix: shared_prefix
        displayName: Beta Widgets
        description: Fixture product.
        status: planned
    """
    _write_domain(tmp_path, "alpha", shared_prefix_a)
    _write_domain(tmp_path, "beta", shared_prefix_b)

    with pytest.raises(DomainDescriptorError, match="asset_prefix 'shared_prefix' is declared by both"):
        discover_products(tmp_path)


def test_domain_descriptor_rejects_path_traversal_in_product_id() -> None:
    """discover_products derives manifest_key as f"floe/manifests/{domain}/{id}/{id}.manifest.json"
    with no further sanitization. An id of "../other" would previously pass the
    "non-empty string" check and escape the product's own directory in that
    derived path (and in Dagster job/namespace names), so it must be rejected
    at validation time instead.
    """
    descriptor = {
        "apiVersion": "openlakeforge.io/v1alpha1",
        "kind": "Domain",
        "name": "sales",
        "displayName": "Sales",
        "description": "Sales",
        "status": "planned",
        "data_products": [
            {
                "id": "../other",
                "name": "sales_orders",
                "displayName": "Orders",
                "description": "Orders",
                "status": "planned",
            }
        ],
    }
    with pytest.raises(DomainDescriptorError, match=r"data_products\[0\]\.id must match"):
        validate_domain_descriptor(descriptor)


def test_domain_descriptor_rejects_unsafe_asset_prefix() -> None:
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
                "asset_prefix": "../escape",
            }
        ],
    }
    with pytest.raises(DomainDescriptorError, match=r"data_products\[0\]\.asset_prefix must match"):
        validate_domain_descriptor(descriptor)
