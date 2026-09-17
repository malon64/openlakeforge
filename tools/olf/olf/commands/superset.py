"""Superset report deploy/export helpers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from openlakeforge_domain import inventory_for

from olf import config, log
from olf.commands._shared import fail

if TYPE_CHECKING:
    from olf.superset import StageReportTarget

app = typer.Typer(help="Superset report deploy/export helpers.")
report_app = typer.Typer(help="Source-controlled Superset report bundles.")


@report_app.command("validate")
def report_validate(
    dashboard: str = typer.Argument(
        "", help="One dashboard declared in lakehouse.yaml; defaults to every declared dashboard."
    ),
    project_root: str = typer.Option(
        "", "--project-root", help="Writable project root; defaults to the current directory."
    ),
) -> None:
    """Check report bundles against the promotion contract: stable identities,
    resolvable references, and no stage-bound or workspace-bound values."""
    from olf import superset
    from olf.commands._project import writable_project_root

    try:
        root = writable_project_root(project_root)
        declared = {item.name: item.report_source_dir for item in inventory_for(root).dashboards}
        if dashboard and dashboard not in declared:
            raise typer.BadParameter(f"{dashboard!r} is not a dashboard declared in lakehouse.yaml")
        # Without a named dashboard this also enforces descriptor/tree parity:
        # an undeclared bundle is never packaged, so validating only what is
        # declared would report a project clean that cannot promote its tree.
        selected = (
            [declared[dashboard]] if dashboard else superset.validate_report_registry(root, tuple(declared.values()))
        )
        errors = superset.validate_report_bundles(root, selected)
    except RuntimeError as exc:
        raise typer.Exit(code=fail(str(exc))) from exc
    for error in errors:
        typer.echo(error)
    if errors:
        raise typer.Exit(code=1)
    typer.echo(f"{len(selected)} report bundle(s) are promotable.")


@app.command("deploy-reports")
def superset_deploy_reports(
    provider: str = typer.Option("local", "--provider", help="Provider owning the deployed contracts."),
    profile: str = typer.Option("", "--profile", help="Deprecated single-DEV preset shorthand: 'full' or 'slim'."),
    namespace: str = typer.Option("", "--namespace", help="Kubernetes namespace override."),
    stage: str = typer.Option(
        "", "--stage", help="Stage whose Superset receives the reports: dev, uat, or prod. Defaults to dev."
    ),
    cluster_name: str = typer.Option("", "--cluster-name", help="Local kind cluster name override."),
    kubeconfig_path: str = typer.Option("", "--kubeconfig-path", help="Kubeconfig file path override."),
    project_root: str = typer.Option(
        "", "--project-root", help="Writable project root; defaults to the current directory."
    ),
) -> None:
    """Build and import reports using the selected provider's Terraform contracts."""
    from olf.commands.runtime import provider_contract_environment

    with provider_contract_environment(
        provider=provider,
        profile=profile,
        namespace=namespace,
        cluster_name=cluster_name,
        kubeconfig_path=kubeconfig_path,
        project_root=project_root,
        stage=stage,
    ):
        deploy_superset_reports(stage=stage)


def deploy_superset_reports(stage: str = "") -> None:
    """Build and import source-controlled Superset report bundles."""
    from olf import superset

    project = config.project_spec()
    target = _report_target(stage)
    inventory = inventory_for(project.root)
    declared_report_dirs = tuple(dashboard.report_source_dir for dashboard in inventory.dashboards)
    override = os.environ.get("SUPERSET_REPORT_SOURCE_DIR") or None
    if override is not None and override not in declared_report_dirs:
        raise typer.BadParameter(f"SUPERSET_REPORT_SOURCE_DIR {override!r} is not declared in lakehouse.yaml")
    log.step(f"Importing Superset reports into stage '{target.stage}' (namespace {target.namespace})")
    superset.deploy_reports(
        project.root,
        target.namespace,
        target.sqlalchemy_uri,
        report_source_dir=override,
        declared_report_dirs=declared_report_dirs,
        work_dir=Path(config.env("SUPERSET_REPORT_WORK_DIR", ".tmp/superset-reports")),
        reports_mount_path=config.env("SUPERSET_REPORTS_MOUNT_PATH", superset.REPORTS_MOUNT_PATH_DEFAULT),
        admin_username=config.env("SUPERSET_ADMIN_USERNAME", "admin"),
        schema_prefix=target.schema_prefix,
    )


@app.command("export-reports")
def superset_export_reports(
    provider: str = typer.Option("local", "--provider", help="Provider owning the deployed contracts."),
    profile: str = typer.Option("", "--profile", help="Deprecated single-DEV preset shorthand: 'full' or 'slim'."),
    namespace: str = typer.Option("", "--namespace", help="Kubernetes namespace override."),
    stage: str = typer.Option(
        ...,
        "--stage",
        help="Stage whose Superset is exported from: dev, uat, or prod. Authoring happens in shared DEV.",
    ),
    cluster_name: str = typer.Option("", "--cluster-name", help="Local kind cluster name override."),
    kubeconfig_path: str = typer.Option("", "--kubeconfig-path", help="Kubeconfig file path override."),
    project_root: str = typer.Option(
        "", "--project-root", help="Writable project root; defaults to the current directory."
    ),
) -> None:
    """Export reports using the selected provider's Terraform contracts."""
    from olf.commands.runtime import provider_contract_environment

    with provider_contract_environment(
        provider=provider,
        profile=profile,
        namespace=namespace,
        cluster_name=cluster_name,
        kubeconfig_path=kubeconfig_path,
        project_root=project_root,
        stage=stage,
    ):
        export_superset_reports(stage=stage)


def export_superset_reports(stage: str = "") -> None:
    """Export a live Superset dashboard back into a source-controlled bundle."""
    import yaml
    from openlakeforge_domain import Dashboard

    from olf import superset

    project = config.project_spec()
    target = _report_target(stage)
    inventory = inventory_for(project.root)
    override = os.environ.get("SUPERSET_REPORT_SOURCE_DIR") or None
    if inventory.dashboards:
        # Unchanged from before #229: the first declared dashboard is always
        # the title/bundle-name source, even when an override targets a
        # different (declared or undeclared) bundle.
        default_dashboard = inventory.dashboards[0]
        default_product = next(
            product for product in inventory.products if product.id == default_dashboard.products[0]
        )
        report_source_dir = override or default_dashboard.report_source_dir
    elif override:
        # Nothing declared yet (e.g. a scaffolded --with-report draft, #205):
        # a named target is still exportable, but only if it already exists,
        # so a typo doesn't silently create a bundle.
        if not (project.root / override).is_dir():
            raise typer.BadParameter(f"SUPERSET_REPORT_SOURCE_DIR {override!r} does not exist")
        report_source_dir = override
        default_dashboard = Dashboard(name=Path(override).name, products=())
        default_product = None
    else:
        raise typer.BadParameter(
            "lakehouse.yaml declares no dashboard to export; "
            "set SUPERSET_REPORT_SOURCE_DIR to target an undeclared bundle"
        )

    def _default_dashboard_title() -> str:
        # Dashboard identity can differ from product metadata (see
        # e2e.discovered_dashboards) — prefer the checked-in bundle's own
        # title so a re-export finds the same dashboard it last exported.
        # Falls back to displayName only when no bundle exists yet to read,
        # and to the bundle's directory name when nothing is declared enough
        # to resolve a product either.
        for dashboard_file in superset.discover_dashboard_files(project.root / report_source_dir):
            document = yaml.safe_load(dashboard_file.read_text())
            title = document.get("dashboard_title") if isinstance(document, dict) else None
            if title:
                return title
        return default_product.display_name if default_product else default_dashboard.name

    log.step(f"Exporting Superset reports from stage '{target.stage}' (namespace {target.namespace})")
    superset.export_report(
        project.root,
        target.namespace,
        report_source_dir=report_source_dir,
        bundle_name=config.env(
            "SUPERSET_REPORT_EXPORT_BUNDLE_NAME", default_dashboard.superset_export_bundle_name
        ),
        work_dir=Path(config.env("SUPERSET_REPORT_WORK_DIR", ".tmp/superset-reports")),
        reports_mount_path=config.env("SUPERSET_REPORTS_MOUNT_PATH", superset.REPORTS_MOUNT_PATH_DEFAULT),
        admin_username=config.env("SUPERSET_ADMIN_USERNAME", "admin"),
        dashboard_title=config.env("SUPERSET_DASHBOARD_TITLE", _default_dashboard_title()),
    )


def _report_target(stage: str) -> StageReportTarget:
    from olf import superset

    try:
        return superset.resolve_stage_report_target(os.environ, stage=stage)
    except superset.ReportStageError as exc:
        raise typer.Exit(code=fail(str(exc))) from exc
