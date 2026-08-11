import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import olf
from olf.cli import app

runner = CliRunner()


def test_version_command_prints_package_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.output.strip() == olf.__version__


def test_revision_compute_command_prints_runtime_artifact_revision(tmp_path: Path) -> None:
    path = tmp_path / "manifests/sales/order_revenue/order_revenue.manifest.json"
    path.parent.mkdir(parents=True)
    path.write_text("{}")

    result = runner.invoke(app, ["revision", "compute", "--runtime-root", str(tmp_path)])

    assert result.exit_code == 0
    assert result.output.startswith("sha256:")


def test_inventory_terraform_external_command_reads_json_query(tmp_path: Path) -> None:
    descriptor = tmp_path / "domains" / "sales" / "domain.yaml"
    descriptor.parent.mkdir(parents=True)
    descriptor.write_text(
        """apiVersion: openlakeforge.io/v1alpha2
kind: Domain
name: sales
displayName: Sales
description: Sales domain.
status: planned
data_products:
  - id: orders
    name: sales_orders
    displayName: Sales Orders
    description: Sales orders.
    status: planned
    asset_prefix: sales_orders
    bronze:
      - name: orders
        path: s3://lakehouse-bronze/sales/orders/orders
    silver_tables:
      tables:
        - name: orders
    gold_tables:
      tables:
        - name: mart_orders
""",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["inventory", "terraform-external"], input=json.dumps({"repo_root": str(tmp_path)}))

    assert result.exit_code == 0
    assert json.loads(result.output)["inventory"]


def test_superset_export_reports_defaults_come_from_the_default_product(monkeypatch: pytest.MonkeyPatch) -> None:
    from olf import cli
    from olf import inventory as inventory_module

    default_product = inventory_module.inventory_for(cli._repo_root()).default_product
    calls: list[dict] = []
    monkeypatch.setattr(
        "olf.superset.export_report",
        lambda *args, **kwargs: calls.append(kwargs),
    )

    result = runner.invoke(app, ["superset", "export-reports"])

    assert result.exit_code == 0
    assert calls[0]["report_source_dir"] == default_product.report_source_dir
    assert calls[0]["bundle_name"] == default_product.superset_export_bundle_name
    assert calls[0]["dashboard_title"] == default_product.display_name


def test_superset_export_reports_dashboard_title_prefers_the_bundles_own_title(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bundle's own dashboard_title must win over displayName when they differ."""
    descriptor = tmp_path / "domains" / "sales" / "domain.yaml"
    descriptor.parent.mkdir(parents=True)
    descriptor.write_text(
        """apiVersion: openlakeforge.io/v1alpha2
kind: Domain
name: sales
displayName: Sales
description: Sales domain.
status: planned
data_products:
  - id: orders
    name: sales_orders
    displayName: Sales Orders Product Metadata Name
    description: Sales orders.
    status: planned
    asset_prefix: sales_orders
    bronze:
      - name: orders
        path: s3://lakehouse-bronze/sales/orders/orders
    silver_tables:
      tables:
        - name: orders
    gold_tables:
      tables:
        - name: mart_orders
""",
        encoding="utf-8",
    )
    dashboards_dir = tmp_path / "domains" / "sales" / "reports" / "superset" / "orders" / "dashboards"
    dashboards_dir.mkdir(parents=True)
    (dashboards_dir / "Live_1.yaml").write_text(
        "dashboard_title: The Actual Live Dashboard Title\nslug: sales-orders-live\n", encoding="utf-8"
    )

    monkeypatch.setenv("OPENLAKEFORGE_REPO_ROOT", str(tmp_path))
    calls: list[dict] = []
    monkeypatch.setattr("olf.superset.export_report", lambda *args, **kwargs: calls.append(kwargs))

    result = runner.invoke(app, ["superset", "export-reports"])

    assert result.exit_code == 0
    assert calls[0]["dashboard_title"] == "The Actual Live Dashboard Title"
