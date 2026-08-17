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


def test_layers_enabled_exits_nonzero_for_a_disabled_layer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENLAKEFORGE_ANALYTICS_ENABLED", "false")

    result = runner.invoke(app, ["layers", "enabled", "--layer", "analytics"])

    assert result.exit_code == 1


def test_artifacts_deploy_optional_layers_skips_disabled_layers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENLAKEFORGE_ANALYTICS_ENABLED", "false")
    monkeypatch.setenv("OPENLAKEFORGE_GOVERNANCE_ENABLED", "false")
    monkeypatch.setattr("olf.cli.deploy_superset_reports", lambda: pytest.fail("reports should be skipped"))
    monkeypatch.setattr("olf.cli.deploy_openmetadata_metadata", lambda: pytest.fail("metadata should be skipped"))

    result = runner.invoke(app, ["artifacts", "deploy-optional-layers"])

    assert result.exit_code == 0
    assert "Skipping Superset report assets" in result.output
    assert "Skipping OpenMetadata governance metadata" in result.output


def test_revision_compute_command_prints_runtime_artifact_revision(tmp_path: Path) -> None:
    path = tmp_path / "manifests/sales/order_revenue/order_revenue.manifest.json"
    path.parent.mkdir(parents=True)
    path.write_text("{}")

    result = runner.invoke(app, ["revision", "compute", "--runtime-root", str(tmp_path)])

    assert result.exit_code == 0
    assert result.output.startswith("sha256:")


def test_superset_export_reports_defaults_come_from_the_default_product(monkeypatch: pytest.MonkeyPatch) -> None:
    from openlakeforge_domain import inventory_for

    from olf import cli

    default_product = inventory_for(cli._repo_root()).default_product
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


def test_catalog_sync_namespaces_dispatches_to_glue_for_aws_glue_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from olf import cli

    calls: list[dict] = []
    monkeypatch.setenv("OPENLAKEFORGE_CATALOG_PROVIDER", "aws-glue")
    monkeypatch.setattr(
        cli,
        "_sync_glue_namespaces",
        lambda *, desired, dry_run, prune: calls.append(
            {"backend": "glue", "dry_run": dry_run, "prune": prune}
        ),
    )
    monkeypatch.setattr(
        cli, "_sync_polaris_namespaces", lambda **kwargs: calls.append({"backend": "polaris"})
    )

    result = runner.invoke(app, ["catalog", "sync-namespaces", "--dry-run"])

    assert result.exit_code == 0
    assert calls == [{"backend": "glue", "dry_run": True, "prune": False}]


def test_catalog_sync_namespaces_dispatches_to_polaris_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from olf import cli

    calls: list[dict] = []
    monkeypatch.delenv("OPENLAKEFORGE_CATALOG_PROVIDER", raising=False)
    monkeypatch.setattr(
        cli,
        "_sync_polaris_namespaces",
        lambda *, desired, dry_run, prune: calls.append(
            {"backend": "polaris", "dry_run": dry_run, "prune": prune}
        ),
    )
    monkeypatch.setattr(
        cli, "_sync_glue_namespaces", lambda **kwargs: calls.append({"backend": "glue"})
    )

    result = runner.invoke(app, ["catalog", "sync-namespaces", "--prune"])

    assert result.exit_code == 0
    assert calls == [{"backend": "polaris", "dry_run": False, "prune": True}]


def test_catalog_sync_namespaces_treats_false_prune_environment_as_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from olf import cli

    calls: list[dict] = []
    monkeypatch.setenv("OPENLAKEFORGE_CATALOG_PRUNE_NAMESPACES", "false")
    monkeypatch.setattr(
        cli,
        "_sync_polaris_namespaces",
        lambda *, desired, dry_run, prune: calls.append({"dry_run": dry_run, "prune": prune}),
    )

    result = runner.invoke(app, ["catalog", "sync-namespaces"])

    assert result.exit_code == 0
    assert calls == [{"dry_run": False, "prune": False}]


def test_catalog_sync_namespaces_rejects_an_unknown_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    from olf import cli

    calls: list[str] = []
    monkeypatch.setenv("OPENLAKEFORGE_CATALOG_PROVIDER", "snowflake")
    monkeypatch.setattr(cli, "_sync_polaris_namespaces", lambda **kwargs: calls.append("polaris"))
    monkeypatch.setattr(cli, "_sync_glue_namespaces", lambda **kwargs: calls.append("glue"))

    result = runner.invoke(app, ["catalog", "sync-namespaces"])

    assert result.exit_code == 1
    assert calls == []
    assert "snowflake" in result.output
