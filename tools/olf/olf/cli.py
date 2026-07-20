"""The olf CLI: entrypoint for OpenLakeForge deployment tooling.

Subcommand groups are registered here as they are implemented:
contracts, floe, artifacts, superset, openmetadata, polaris, k8s, e2e.
"""

from __future__ import annotations

import os
from pathlib import Path

import typer

import olf
from olf import config
from olf import contracts as contracts_module
from olf import floe as floe_module

app = typer.Typer(
    name="olf",
    help="OpenLakeForge deployment tooling.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)

contracts_app = typer.Typer(help="Provider-contract runtime environment helpers.")
floe_app = typer.Typer(help="Floe profile and manifest helpers.")
artifacts_app = typer.Typer(help="Object-storage artifact helpers.")
superset_app = typer.Typer(help="Superset report deploy/export helpers.")
openmetadata_app = typer.Typer(help="OpenMetadata governance metadata helpers.")
k8s_app = typer.Typer(help="Kubernetes image bookkeeping helpers.")
polaris_app = typer.Typer(help="Polaris catalog credential helpers.")
e2e_app = typer.Typer(help="End-to-end environment validation.")
app.add_typer(contracts_app, name="contracts")
app.add_typer(floe_app, name="floe")
app.add_typer(artifacts_app, name="artifacts")
app.add_typer(superset_app, name="superset")
app.add_typer(openmetadata_app, name="openmetadata")
app.add_typer(k8s_app, name="k8s")
app.add_typer(polaris_app, name="polaris")
app.add_typer(e2e_app, name="e2e")


def _repo_root() -> Path:
    return Path(os.environ.get("OPENLAKEFORGE_REPO_ROOT", ".")).resolve()


@app.callback()
def _root() -> None:
    """OpenLakeForge deployment tooling."""


@app.command()
def version() -> None:
    """Print the tooling version."""
    typer.echo(olf.__version__)


@contracts_app.command("env")
def contracts_env(
    terraform_dir: str = typer.Option(
        "infra/terraform/environments/local",
        "--terraform-dir",
        help="Terraform root exposing the provider_contracts output.",
    ),
) -> None:
    """Print `export`/`unset` lines for the runtime contract environment.

    Falls back to local defaults when the Terraform stack has not been applied
    yet. Intended for `eval` from scripts/contracts/load-runtime-env.sh.
    """
    provider_contracts = contracts_module.load_provider_contracts(terraform_dir)
    exports, unsets = contracts_module.build_contract_env(os.environ, provider_contracts)
    output = contracts_module.render_shell_exports(exports, unsets)
    if output:
        typer.echo(output)


@floe_app.command("render-profile")
def floe_render_profile() -> None:
    """Render the Floe EnvironmentProfile YAML for the active contract env."""
    typer.echo(floe_module.render_profile(os.environ), nl=False)


@artifacts_app.command("upload-manifests")
def artifacts_upload_manifests(
    via: str = typer.Option(
        "port-forward",
        "--via",
        help="'port-forward' for in-cluster S3-compatible storage, 'direct' for cloud S3.",
    ),
    manifest_root: str = typer.Option(
        "",
        "--manifest-root",
        help="Rendered-manifest directory for --via direct (default: .tmp/floe-runtime/aws/manifests).",
    ),
    runtime_root: str = typer.Option(
        "",
        "--runtime-root",
        help="Rendered Floe runtime artifact root containing configs/, profiles/, and manifests/.",
    ),
) -> None:
    """Publish product Floe runtime artifacts to the operational artifact bucket."""
    from olf import s3

    repo_root = _repo_root()
    namespace = config.namespace()
    bucket = config.env("OPENLAKEFORGE_OPS_BUCKET_NAME") or config.env("OPENLAKEFORGE_ARTIFACT_BUCKET_NAME")
    if not bucket:
        raise typer.Exit(code=_fail("no ops/artifact bucket resolved from the contract environment."))

    if via == "direct":
        if runtime_root:
            uploads = s3.discover_runtime_artifacts(Path(runtime_root))
        else:
            root = Path(manifest_root) if manifest_root else repo_root / ".tmp/floe-runtime/aws/manifests"
            uploads = s3.discover_runtime_manifests(root)
        if not uploads:
            root = runtime_root or manifest_root or str(repo_root / ".tmp/floe-runtime/aws/manifests")
            raise typer.Exit(code=_fail(f"no rendered Floe artifacts found under {root}."))
        s3.upload_direct(bucket, uploads, region=config.env("OPENLAKEFORGE_STORAGE_REGION"))
    elif via == "port-forward":
        if runtime_root:
            uploads = s3.discover_runtime_artifacts(Path(runtime_root))
        else:
            uploads = s3.discover_tracked_manifests(repo_root)
        if not uploads:
            raise typer.Exit(code=_fail("no generated product Floe artifacts found. Run 'make floe-manifest' first."))
        secret_name = config.env("OPENLAKEFORGE_STORAGE_CREDENTIALS_SECRET_NAME")
        service = config.env("OPENLAKEFORGE_STORAGE_S3_SERVICE_NAME", "seaweedfs-s3")
        remote_port = int(config.env("OPENLAKEFORGE_STORAGE_S3_SERVICE_PORT", "8333"))
        from olf import k8s

        s3.upload_via_port_forward(
            bucket,
            uploads,
            service=service,
            remote_port=remote_port,
            namespace=namespace,
            access_key_id=k8s.secret_value(
                secret_name, config.env("OPENLAKEFORGE_STORAGE_ACCESS_KEY_ID_KEY", "AWS_ACCESS_KEY_ID"), namespace
            ),
            secret_access_key=k8s.secret_value(
                secret_name,
                config.env("OPENLAKEFORGE_STORAGE_SECRET_ACCESS_KEY_KEY", "AWS_SECRET_ACCESS_KEY"),
                namespace,
            ),
            region=config.env("OPENLAKEFORGE_STORAGE_REGION", "us-east-1"),
        )
    else:
        raise typer.Exit(code=_fail(f"unknown --via mode: {via!r} (expected 'port-forward' or 'direct')."))


@superset_app.command("deploy-reports")
def superset_deploy_reports() -> None:
    """Build and import source-controlled Superset report bundles."""
    from olf import superset

    superset.deploy_reports(
        _repo_root(),
        config.namespace(),
        config.env("OPENLAKEFORGE_QUERY_SQLALCHEMY_URI"),
        report_source_dir=os.environ.get("SUPERSET_REPORT_SOURCE_DIR") or None,
        work_dir=Path(config.env("SUPERSET_REPORT_WORK_DIR", ".tmp/superset-reports")),
        reports_mount_path=config.env("SUPERSET_REPORTS_MOUNT_PATH", superset.REPORTS_MOUNT_PATH_DEFAULT),
        admin_username=config.env("SUPERSET_ADMIN_USERNAME", "admin"),
    )


@superset_app.command("export-reports")
def superset_export_reports() -> None:
    """Export a live Superset dashboard back into a source-controlled bundle."""
    from olf import superset

    superset.export_report(
        _repo_root(),
        config.namespace(),
        report_source_dir=config.env(
            "SUPERSET_REPORT_SOURCE_DIR", "domains/sales/reports/superset/order_revenue"
        ),
        bundle_name=config.env(
            "SUPERSET_REPORT_EXPORT_BUNDLE_NAME", "sales_order_revenue_superset_assets_export.zip"
        ),
        work_dir=Path(config.env("SUPERSET_REPORT_WORK_DIR", ".tmp/superset-reports")),
        reports_mount_path=config.env("SUPERSET_REPORTS_MOUNT_PATH", superset.REPORTS_MOUNT_PATH_DEFAULT),
        admin_username=config.env("SUPERSET_ADMIN_USERNAME", "admin"),
        dashboard_title=config.env("SUPERSET_DASHBOARD_TITLE", "Sales Order Revenue"),
    )


@openmetadata_app.command("deploy-metadata")
def openmetadata_deploy_metadata() -> None:
    """Seed OpenMetadata domains, data products, and medallion containers."""
    from olf import k8s
    from olf import openmetadata as om

    namespace = config.namespace()
    service = config.env("OPENMETADATA_SERVICE", "openmetadata")
    remote_port = int(config.env("OPENMETADATA_SERVICE_PORT", "8585"))

    log_step(f"Waiting for OpenMetadata deployment {service}...")
    k8s.wait_for_rollout(f"deployment/{service}", namespace)

    log_prefix = config.env(
        "OPENLAKEFORGE_PORT_FORWARD_LOG_PREFIX", "/tmp/openlakeforge"
    )
    log_path = f"{log_prefix}-openmetadata-port-forward.log"
    with k8s.port_forward(service, remote_port, namespace, log_path=log_path) as local_port:
        cfg = om.OpenMetadataConfig.from_environment(
            os.environ,
            base_url=f"http://127.0.0.1:{local_port}",
            admin_email=config.env("OPENMETADATA_ADMIN_EMAIL", "admin@open-metadata.org"),
            admin_password=config.env("OPENMETADATA_ADMIN_PASSWORD", "admin"),
            metadata_root=config.env("OPENMETADATA_METADATA_ROOT", "domains"),
            metadata_source_dir=os.environ.get("OPENMETADATA_METADATA_SOURCE_DIR", ""),
            allow_missing_assets=_truthy(config.env("OPENMETADATA_ALLOW_MISSING_ASSETS", "false")),
            catalog_service=config.env("OPENMETADATA_CATALOG_SERVICE") or config.env("OPENLAKEFORGE_CATALOG_PROVIDER"),
            catalog_database=config.env("OPENMETADATA_CATALOG_DATABASE") or config.env("OPENLAKEFORGE_CATALOG_NAME"),
            cleanup_legacy_default_database=_truthy(
                config.env("OPENMETADATA_CLEANUP_LEGACY_DEFAULT_DATABASE", "true")
            ),
        )
        try:
            om.OpenMetadataDeployer(cfg, om.OpenMetadataClient(cfg.base_url)).deploy()
        except om.OpenMetadataError as exc:
            raise typer.Exit(code=_fail(str(exc))) from exc
    typer.echo("Deployed OpenMetadata governance metadata.")


@k8s_app.command("set-project-code-image")
def k8s_set_project_code_image(
    image: str = typer.Option(..., "--image", help="Fully qualified project-code image reference."),
    timeout: str = typer.Option("600s", "--timeout", help="Timeout for each Dagster deployment rollout."),
) -> None:
    """Point every Dagster surface at an image and wait for one rollout."""
    from olf import k8s

    k8s.set_project_code_image(image, config.namespace(), rollout_timeout=timeout)


@polaris_app.command("check-credentials")
def polaris_check_credentials(
    service: str = typer.Option("polaris", "--service"),
    secret: str = typer.Option("polaris-om-creds", "--secret"),
    client_id_key: str = typer.Option("POLARIS_OM_CLIENT_ID", "--client-id-key"),
    client_secret_key: str = typer.Option("POLARIS_OM_CLIENT_SECRET", "--client-secret-key"),
    scope: str = typer.Option("PRINCIPAL_ROLE:ALL", "--scope"),
    remote_port: int = typer.Option(8181, "--remote-port"),
) -> None:
    """Preflight Polaris service-principal credentials through a port-forward.

    Exit codes: 0 = valid (or unreachable/unknown, left unchanged),
    3 = stale (HTTP 401) so the caller should force a Polaris rebootstrap.
    """
    from olf import k8s, log, polaris

    namespace = config.namespace()
    if not k8s.resource_exists("service", service, namespace) or not k8s.resource_exists(
        "secret", secret, namespace
    ):
        return

    client_id = k8s.secret_value(secret, client_id_key, namespace)
    client_secret = k8s.secret_value(secret, client_secret_key, namespace)

    log_prefix = config.env(
        "OPENLAKEFORGE_PORT_FORWARD_LOG_PREFIX", "/tmp/openlakeforge"
    )
    log_path = f"{log_prefix}-polaris-port-forward.log"
    with k8s.port_forward(service, remote_port, namespace, log_path=log_path) as local_port:
        base = f"http://127.0.0.1:{local_port}"
        k8s.http_wait(f"{base}/q/health", attempts=30, delay=1.0)
        status = polaris.request_token_status(
            f"{base}/api/catalog/v1/oauth/tokens", client_id, client_secret, scope
        )

    if status == 200:
        return
    if status == 401:
        log.warn("Polaris service-principal credentials are stale; forcing Polaris bootstrap.")
        raise typer.Exit(code=polaris.STALE_EXIT_CODE)
    log.warn(f"Polaris credential preflight returned HTTP {status}; leaving bootstrap generation unchanged.")


@e2e_app.command("run")
def e2e_run(
    env: str = typer.Option(..., "--env", help="Environment to validate: local, azure, or aws."),
    suite: str = typer.Option("", "--suite", help="Suite to run: full or smoke. Defaults to full."),
) -> None:
    """Run end-to-end validation for a deployed OpenLakeForge environment."""
    from olf import e2e

    valid_envs = {"local", "azure", "aws"}
    valid_suites = {"", "full", "smoke"}
    if env not in valid_envs:
        raise typer.Exit(code=_fail(f"unknown --env {env!r}; expected one of: {', '.join(sorted(valid_envs))}."))
    if suite not in valid_suites:
        raise typer.Exit(code=_fail(f"unknown --suite {suite!r}; expected 'full' or 'smoke'."))
    try:
        e2e.run(
            env,  # type: ignore[arg-type]
            suite=suite or None,  # type: ignore[arg-type]
            namespace=config.namespace(),
            kube_context=config.env("KUBE_CONTEXT"),
        )
    except e2e.E2EError as exc:
        raise typer.Exit(code=_fail(str(exc))) from exc


def _truthy(value: str) -> bool:
    return value.lower() in {"1", "true", "yes", "y"}


def log_step(message: str) -> None:
    from olf import log

    log.step(message)


def _fail(message: str) -> int:
    from olf import log

    log.error(message)
    return 1


def main() -> None:
    app()


if __name__ == "__main__":
    main()
