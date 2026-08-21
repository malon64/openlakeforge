"""Superset report deploy/export helpers."""

from __future__ import annotations

import os
from pathlib import Path

import typer
from openlakeforge_domain import inventory_for

from olf import config

app = typer.Typer(help="Superset report deploy/export helpers.")


@app.command("deploy-reports")
def superset_deploy_reports() -> None:
    """Build and import source-controlled Superset report bundles."""
    deploy_superset_reports()


def deploy_superset_reports() -> None:
    """Build and import source-controlled Superset report bundles."""
    from olf import superset

    inventory = inventory_for(config.repo_root())
    declared_report_dirs = tuple(dashboard.report_source_dir for dashboard in inventory.dashboards)
    override = os.environ.get("SUPERSET_REPORT_SOURCE_DIR") or None
    if override is not None and override not in declared_report_dirs:
        raise typer.BadParameter(f"SUPERSET_REPORT_SOURCE_DIR {override!r} is not declared in lakehouse.yaml")
    superset.deploy_reports(
        config.repo_root(),
        config.namespace(),
        config.env("OPENLAKEFORGE_QUERY_SQLALCHEMY_URI"),
        report_source_dir=override,
        declared_report_dirs=declared_report_dirs,
        work_dir=Path(config.env("SUPERSET_REPORT_WORK_DIR", ".tmp/superset-reports")),
        reports_mount_path=config.env("SUPERSET_REPORTS_MOUNT_PATH", superset.REPORTS_MOUNT_PATH_DEFAULT),
        admin_username=config.env("SUPERSET_ADMIN_USERNAME", "admin"),
    )


@app.command("export-reports")
def superset_export_reports() -> None:
    """Export a live Superset dashboard back into a source-controlled bundle."""
    import yaml

    from olf import superset

    repo_root = config.repo_root()
    inventory = inventory_for(repo_root)
    if not inventory.dashboards:
        raise typer.BadParameter("lakehouse.yaml declares no dashboard to export")
    default_dashboard = inventory.dashboards[0]
    default_product = next(product for product in inventory.products if product.id == default_dashboard.products[0])
    default_report_source_dir = default_dashboard.report_source_dir
    report_source_dir = config.env("SUPERSET_REPORT_SOURCE_DIR", default_report_source_dir)

    def _default_dashboard_title() -> str:
        # Dashboard identity can differ from product metadata (see
        # e2e.discovered_dashboards) — prefer the checked-in bundle's own
        # title so a re-export finds the same dashboard it last exported.
        # Falls back to displayName only when no bundle exists yet to read.
        for dashboard_file in superset.discover_dashboard_files(repo_root / report_source_dir):
            document = yaml.safe_load(dashboard_file.read_text())
            title = document.get("dashboard_title") if isinstance(document, dict) else None
            if title:
                return title
        return default_product.display_name

    superset.export_report(
        repo_root,
        config.namespace(),
        report_source_dir=report_source_dir,
        bundle_name=config.env(
            "SUPERSET_REPORT_EXPORT_BUNDLE_NAME", default_dashboard.superset_export_bundle_name
        ),
        work_dir=Path(config.env("SUPERSET_REPORT_WORK_DIR", ".tmp/superset-reports")),
        reports_mount_path=config.env("SUPERSET_REPORTS_MOUNT_PATH", superset.REPORTS_MOUNT_PATH_DEFAULT),
        admin_username=config.env("SUPERSET_ADMIN_USERNAME", "admin"),
        dashboard_title=config.env("SUPERSET_DASHBOARD_TITLE", _default_dashboard_title()),
    )
