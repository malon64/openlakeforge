import json
from pathlib import Path

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
