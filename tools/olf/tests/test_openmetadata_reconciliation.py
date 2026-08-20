from pathlib import Path

import pytest

from olf.clients.openmetadata import OpenMetadataClient, OpenMetadataError
from olf.openmetadata._config import OpenMetadataConfig
from olf.openmetadata._reconciliation import OpenMetadataReconciler


def _single_product_reconciler(tmp_path: Path) -> OpenMetadataReconciler:
    (tmp_path / "bronze" / "crm").mkdir(parents=True)
    (tmp_path / "bronze" / "crm" / "source.yaml").write_text(
        """apiVersion: openlakeforge.io/v1alpha3
kind: Source
name: crm
displayName: CRM
description: crm source.
status: active
resources:
  - name: orders
    description: Raw sales orders.
"""
    )
    (tmp_path / "lakehouse.yaml").write_text(
        """apiVersion: openlakeforge.io/v1alpha3
kind: Lakehouse
name: test
displayName: Test
description: Test lakehouse.
status: active
sources:
  - crm
domains:
  - name: sales
    displayName: Sales
    description: Sales domain
    status: active
    products:
      - id: order_revenue
        displayName: Sales Order Revenue
        description: Revenue from orders.
        status: active
        bronze:
          source: crm
          resources:
            - orders
        silver_tables:
          tables:
            - name: raw_orders
            - name: raw_order_lines
        gold_tables:
          tables:
            - name: mart_order_revenue
dashboards: []
"""
    )
    cfg = OpenMetadataConfig.from_environment(
        {},
        base_url="http://x",
        admin_email="a",
        admin_password="p",
        metadata_root=str(tmp_path),
        metadata_source_dir="",
        allow_missing_assets=False,
        catalog_service="polaris",
        catalog_database="lakehouse_dev",
        cleanup_legacy_default_database=False,
    )
    return OpenMetadataReconciler(cfg, OpenMetadataClient(cfg.base_url))


def test_ensure_database_schema_creates_the_schema_a_table_will_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase 1 stopped pre-creating databaseSchemas when Polaris namespace
    lifecycle moved to Phase 2, so seeding has to create them itself."""
    reconciler = _single_product_reconciler(tmp_path)
    calls: list[tuple[str, dict]] = []

    def request(method: str, path: str, *, payload=None, **_kwargs):
        calls.append((path, payload or {}))
        return {}

    monkeypatch.setattr(reconciler.client, "request", request)

    reconciler.ensure_database_schema("polaris.lakehouse_dev.sales_order_revenue_silver")

    assert calls == [
        (
            "/api/v1/databaseSchemas",
            {"name": "sales_order_revenue_silver", "database": "polaris.lakehouse_dev"},
        )
    ]


def test_ensure_database_schema_is_created_once_per_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reconciler = _single_product_reconciler(tmp_path)
    calls: list[str] = []

    monkeypatch.setattr(
        reconciler.client, "request", lambda method, path, **_kwargs: calls.append(path) or {}
    )

    reconciler.ensure_database_schema("polaris.lakehouse_dev.sales_order_revenue_silver")
    reconciler.ensure_database_schema("polaris.lakehouse_dev.sales_order_revenue_silver")

    assert calls == ["/api/v1/databaseSchemas"]


def test_ensure_database_schema_rejects_a_malformed_fqn(tmp_path: Path) -> None:
    reconciler = _single_product_reconciler(tmp_path)

    with pytest.raises(OpenMetadataError, match="Malformed schema FQN"):
        reconciler.ensure_database_schema("sales_order_revenue_silver")
