from __future__ import annotations

from pathlib import Path

from olf import catalog
from olf.inventory import CatalogNamespace

REPO_ROOT = Path(__file__).resolve().parents[3]


def ns(name: str, location: str) -> CatalogNamespace:
    return CatalogNamespace(name=name, location=location)


def test_plan_creates_namespaces_absent_from_the_catalog() -> None:
    plan = catalog.plan_namespace_sync({}, [ns("sales_silver", "s3://silver/sales_silver/")])

    assert [namespace.name for namespace in plan.create] == ["sales_silver"]
    assert plan.update == ()
    assert plan.delete == ()
    assert not plan.is_empty


def test_plan_leaves_matching_namespaces_alone() -> None:
    desired = [ns("sales_silver", "s3://silver/sales_silver/")]
    existing = {
        "sales_silver": catalog.NamespaceState(
            "s3://silver/sales_silver/", {catalog.MANAGED_BY_KEY: catalog.MANAGED_BY_VALUE}
        )
    }
    plan = catalog.plan_namespace_sync(existing, desired)

    assert plan.create == ()
    assert plan.update == ()
    assert plan.unchanged == ("sales_silver",)
    assert plan.is_empty


def test_plan_relocates_a_namespace_whose_location_drifted() -> None:
    desired = [ns("sales_silver", "s3://new-silver/sales_silver/")]
    existing = {
        "sales_silver": catalog.NamespaceState(
            "s3://old-silver/sales_silver/", {catalog.MANAGED_BY_KEY: catalog.MANAGED_BY_VALUE}
        )
    }
    plan = catalog.plan_namespace_sync(existing, desired)

    assert plan.create == ()
    assert [namespace.location for namespace in plan.update] == ["s3://new-silver/sales_silver/"]


def test_plan_reports_orphans_but_keeps_them_without_prune() -> None:
    existing = {
        "retired_silver": catalog.NamespaceState(
            "s3://silver/retired_silver/", {catalog.MANAGED_BY_KEY: catalog.MANAGED_BY_VALUE}
        )
    }
    plan = catalog.plan_namespace_sync(existing, [])

    assert plan.orphans == ("retired_silver",)
    assert plan.delete == ()
    assert plan.is_empty
    assert "undeclared managed retired_silver" in catalog.render_plan(plan, prune=False)


def test_plan_deletes_orphans_only_when_pruning() -> None:
    existing = {
        "retired_silver": catalog.NamespaceState(
            "s3://silver/retired_silver/", {catalog.MANAGED_BY_KEY: catalog.MANAGED_BY_VALUE}
        )
    }
    plan = catalog.plan_namespace_sync(existing, [], prune=True)

    assert plan.delete == ("retired_silver",)
    assert not plan.is_empty
    assert "- remove metadata retired_silver" in catalog.render_plan(plan, prune=True)


def test_plan_never_prunes_a_foreign_undeclared_namespace() -> None:
    plan = catalog.plan_namespace_sync({"shared": "s3://someone-else/shared/"}, [], prune=True)

    assert plan.delete == ()
    assert plan.foreign == ("shared",)


def test_plan_adopts_only_location_matching_legacy_namespace() -> None:
    plan = catalog.plan_namespace_sync(
        {"sales_silver": "s3://silver/sales_silver/"}, [ns("sales_silver", "s3://silver/sales_silver/")]
    )
    assert [item.name for item in plan.adopt] == ["sales_silver"]


def test_plan_refuses_to_relocate_foreign_namespace() -> None:
    import pytest

    with pytest.raises(catalog.NamespaceSyncError, match="not managed"):
        catalog.plan_namespace_sync(
            {"sales_silver": "s3://other/sales_silver/"}, [ns("sales_silver", "s3://silver/sales_silver/")]
        )


def test_desired_namespaces_follow_the_repository_descriptors() -> None:
    namespaces = catalog.desired_namespaces(
        REPO_ROOT, silver_bucket="lakehouse-silver", gold_bucket="lakehouse-gold"
    )

    by_name = {namespace.name: namespace.location for namespace in namespaces}
    assert by_name["sales_order_revenue_silver"] == "s3://lakehouse-silver/sales_order_revenue_silver/"
    assert by_name["sales_order_revenue_gold"] == "s3://lakehouse-gold/sales_order_revenue_gold/"
    assert "supply_chain_inventory_reliability_silver" in by_name


def test_desired_namespaces_honour_custom_buckets() -> None:
    namespaces = catalog.desired_namespaces(REPO_ROOT, silver_bucket="poc-silver", gold_bucket="poc-gold")

    locations = {namespace.location for namespace in namespaces}
    assert all(location.startswith(("s3://poc-silver/", "s3://poc-gold/")) for location in locations)


class FakeClient:
    """Records the reconciliation calls apply_namespace_sync makes.

    Deliberately provider-agnostic: this is the NamespaceClient protocol both
    olf.polaris.PolarisClient and olf.glue.GlueClient implement.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def create_namespace(self, namespace: CatalogNamespace) -> None:
        self.calls.append(("create", namespace.name))

    def update_namespace_location(self, namespace: CatalogNamespace) -> None:
        self.calls.append(("update", namespace.name))

    def adopt_namespace(self, namespace: CatalogNamespace) -> None:
        self.calls.append(("adopt", namespace.name))

    def drop_namespace(self, name: str) -> None:
        self.calls.append(("drop", name))


def test_apply_creates_before_it_drops() -> None:
    plan = catalog.NamespaceSyncPlan(
        create=(ns("new_silver", "s3://silver/new_silver/"),),
        update=(ns("moved_gold", "s3://gold/moved_gold/"),),
        delete=("retired_silver",),
    )
    client = FakeClient()

    catalog.apply_namespace_sync(client, plan)

    assert client.calls == [
        ("create", "new_silver"),
        ("update", "moved_gold"),
        ("drop", "retired_silver"),
    ]
