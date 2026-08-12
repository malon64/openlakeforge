from __future__ import annotations

import json
from pathlib import Path

import pytest

from olf import polaris
from olf.inventory import CatalogNamespace

REPO_ROOT = Path(__file__).resolve().parents[3]


def ns(name: str, location: str) -> CatalogNamespace:
    return CatalogNamespace(name=name, location=location)


def test_plan_creates_namespaces_absent_from_the_catalog() -> None:
    plan = polaris.plan_namespace_sync({}, [ns("sales_silver", "s3://silver/sales_silver/")])

    assert [namespace.name for namespace in plan.create] == ["sales_silver"]
    assert plan.update == ()
    assert plan.delete == ()
    assert not plan.is_empty


def test_plan_leaves_matching_namespaces_alone() -> None:
    desired = [ns("sales_silver", "s3://silver/sales_silver/")]
    plan = polaris.plan_namespace_sync({"sales_silver": "s3://silver/sales_silver/"}, desired)

    assert plan.create == ()
    assert plan.update == ()
    assert plan.unchanged == ("sales_silver",)
    assert plan.is_empty


def test_plan_relocates_a_namespace_whose_location_drifted() -> None:
    desired = [ns("sales_silver", "s3://new-silver/sales_silver/")]
    plan = polaris.plan_namespace_sync({"sales_silver": "s3://old-silver/sales_silver/"}, desired)

    assert plan.create == ()
    assert [namespace.location for namespace in plan.update] == ["s3://new-silver/sales_silver/"]


def test_plan_reports_orphans_but_keeps_them_without_prune() -> None:
    plan = polaris.plan_namespace_sync({"retired_silver": "s3://silver/retired_silver/"}, [])

    assert plan.orphans == ("retired_silver",)
    assert plan.delete == ()
    assert plan.is_empty
    assert "undeclared retired_silver" in polaris.render_plan(plan, prune=False)


def test_plan_deletes_orphans_only_when_pruning() -> None:
    plan = polaris.plan_namespace_sync(
        {"retired_silver": "s3://silver/retired_silver/"}, [], prune=True
    )

    assert plan.delete == ("retired_silver",)
    assert not plan.is_empty
    assert "- drop retired_silver" in polaris.render_plan(plan, prune=True)


def test_desired_namespaces_follow_the_repository_descriptors() -> None:
    namespaces = polaris.desired_namespaces(
        REPO_ROOT, silver_bucket="lakehouse-silver", gold_bucket="lakehouse-gold"
    )

    by_name = {namespace.name: namespace.location for namespace in namespaces}
    assert by_name["sales_order_revenue_silver"] == "s3://lakehouse-silver/sales_order_revenue_silver/"
    assert by_name["sales_order_revenue_gold"] == "s3://lakehouse-gold/sales_order_revenue_gold/"
    assert "supply_chain_inventory_reliability_silver" in by_name


def test_desired_namespaces_honour_custom_buckets() -> None:
    namespaces = polaris.desired_namespaces(REPO_ROOT, silver_bucket="poc-silver", gold_bucket="poc-gold")

    locations = {namespace.location for namespace in namespaces}
    assert all(location.startswith(("s3://poc-silver/", "s3://poc-gold/")) for location in locations)


class FakeClient:
    """Records the reconciliation calls apply_namespace_sync makes."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def create_namespace(self, namespace: CatalogNamespace) -> None:
        self.calls.append(("create", namespace.name))

    def update_namespace_location(self, namespace: CatalogNamespace) -> None:
        self.calls.append(("update", namespace.name))

    def drop_namespace(self, name: str) -> None:
        self.calls.append(("drop", name))


def test_apply_creates_before_it_drops() -> None:
    plan = polaris.NamespaceSyncPlan(
        create=(ns("new_silver", "s3://silver/new_silver/"),),
        update=(ns("moved_gold", "s3://gold/moved_gold/"),),
        delete=("retired_silver",),
    )
    client = FakeClient()

    polaris.apply_namespace_sync(client, plan)

    assert client.calls == [
        ("create", "new_silver"),
        ("update", "moved_gold"),
        ("drop", "retired_silver"),
    ]


class FakeResponse:
    def __init__(self, body: dict, status: int = 200) -> None:
        self._body = json.dumps(body).encode("utf-8")
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def test_list_namespaces_skips_nested_namespaces(monkeypatch: pytest.MonkeyPatch) -> None:
    config = polaris.PolarisConfig(
        base_url="http://127.0.0.1:8181",
        catalog_name="lakehouse_dev",
        client_id="id",
        client_secret="secret",
    )
    client = polaris.PolarisClient(config, token="t")

    def fake_urlopen(request, timeout=0):  # noqa: ANN001, ARG001 - urllib signature
        if request.full_url.endswith("/namespaces"):
            return FakeResponse({"namespaces": [["sales_silver"], ["sales", "nested"]]})
        return FakeResponse({"properties": {"location": "s3://silver/sales_silver/"}})

    monkeypatch.setattr(polaris.urllib.request, "urlopen", fake_urlopen)

    assert client.list_namespaces() == {"sales_silver": "s3://silver/sales_silver/"}
