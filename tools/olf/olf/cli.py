"""The olf CLI: entrypoint for OpenLakeForge deployment tooling.

Subcommand groups are registered here as they are implemented:
contracts, catalog, floe, artifacts, revision, superset, openmetadata, k8s, e2e, release.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import boto3
import typer
from openlakeforge_domain import inventory_for

import olf
from olf import config
from olf import contracts as contracts_module
from olf import floe as floe_module
from olf import layers as layers_module
from olf import smoke as smoke_module

app = typer.Typer(
    name="olf",
    help="OpenLakeForge deployment tooling.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)

contracts_app = typer.Typer(help="Provider-contract runtime environment helpers.")
catalog_app = typer.Typer(help="Iceberg catalog namespace reconciliation.")
floe_app = typer.Typer(help="Floe profile and manifest helpers.")
artifacts_app = typer.Typer(help="Object-storage artifact helpers.")
layers_app = typer.Typer(help="Optional platform-layer capability helpers.")
revision_app = typer.Typer(help="Immutable Floe runtime-artifact revision helpers.")
superset_app = typer.Typer(help="Superset report deploy/export helpers.")
openmetadata_app = typer.Typer(help="OpenMetadata governance metadata helpers.")
k8s_app = typer.Typer(help="Kubernetes image bookkeeping helpers.")
e2e_app = typer.Typer(help="End-to-end environment validation.")
smoke_app = typer.Typer(help="Bounded deployment smoke helpers.")
release_app = typer.Typer(help="Release manifest, checksums, compatibility matrix, and readiness gate.")
install_app = typer.Typer(help="Consumer install of a tagged release into an existing cluster.")
app.add_typer(contracts_app, name="contracts")
app.add_typer(catalog_app, name="catalog")
app.add_typer(floe_app, name="floe")
app.add_typer(artifacts_app, name="artifacts")
app.add_typer(layers_app, name="layers")
app.add_typer(revision_app, name="revision")
app.add_typer(superset_app, name="superset")
app.add_typer(openmetadata_app, name="openmetadata")
app.add_typer(k8s_app, name="k8s")
app.add_typer(e2e_app, name="e2e")
app.add_typer(smoke_app, name="smoke")
app.add_typer(release_app, name="release")
app.add_typer(install_app, name="install")


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
    exports, unsets = contracts_module.build_contract_env(os.environ, provider_contracts, repo_root=_repo_root())
    output = contracts_module.render_shell_exports(exports, unsets)
    if output:
        typer.echo(output)


def _reconcile_and_report(
    label: str,
    client: Any,
    existing: dict,
    desired: tuple,
    *,
    dry_run: bool,
    prune: bool,
) -> None:
    from olf import catalog as catalog_module

    plan = catalog_module.plan_namespace_sync(existing, desired, prune=prune)
    typer.echo(catalog_module.render_plan(plan, prune=prune))
    if dry_run:
        typer.echo("Dry run: the catalog was not changed.")
        return
    if plan.is_empty:
        typer.echo(f"{label} namespaces already match the descriptors.")
        return
    catalog_module.apply_namespace_sync(client, plan)
    typer.echo(
        f"Synced {label} namespaces: {len(plan.create)} created, {len(plan.adopt)} adopted, "
        f"{len(plan.update)} updated, {len(plan.delete)} metadata-removed."
    )


def _sync_polaris_namespaces(*, desired: tuple, dry_run: bool, prune: bool) -> None:
    from olf import k8s
    from olf import polaris as polaris_module

    rest_uri = config.env("OPENLAKEFORGE_CATALOG_REST_URI", "http://polaris:8181/api/catalog")
    parsed = urlparse(rest_uri)
    if not parsed.hostname:
        raise typer.Exit(code=_fail(f"OPENLAKEFORGE_CATALOG_REST_URI {rest_uri!r} has no host to port-forward to."))
    service = parsed.hostname
    remote_port = parsed.port or 8181

    namespace = config.namespace()
    secret_name = config.env("OPENLAKEFORGE_CATALOG_DEPLOYER_CREDENTIALS_SECRET_NAME", "polaris-deployer-creds")
    client_id_key = config.env("OPENLAKEFORGE_CATALOG_DEPLOYER_CLIENT_ID_KEY", "POLARIS_DEPLOYER_CLIENT_ID")
    client_secret_key = config.env(
        "OPENLAKEFORGE_CATALOG_DEPLOYER_CLIENT_SECRET_KEY", "POLARIS_DEPLOYER_CLIENT_SECRET"
    )

    log_prefix = config.env("OPENLAKEFORGE_PORT_FORWARD_LOG_PREFIX", "/tmp/openlakeforge")
    with k8s.port_forward(
        service, remote_port, namespace, log_path=f"{log_prefix}-polaris-port-forward.log"
    ) as local_port:
        client = polaris_module.PolarisClient(
            polaris_module.PolarisConfig(
                base_url=f"http://127.0.0.1:{local_port}",
                catalog_name=config.env("OPENLAKEFORGE_CATALOG_NAME", "lakehouse_dev"),
                client_id=k8s.secret_value(secret_name, client_id_key, namespace),
                client_secret=k8s.secret_value(secret_name, client_secret_key, namespace),
                oauth_scope=config.env("OPENLAKEFORGE_CATALOG_OAUTH_SCOPE", "PRINCIPAL_ROLE:ALL"),
            )
        )
        try:
            client.login()
            _reconcile_and_report(
                "Polaris", client, client.list_namespaces(), desired, dry_run=dry_run, prune=prune
            )
        except polaris_module.PolarisError as exc:
            raise typer.Exit(code=_fail(str(exc))) from exc


def _sync_glue_namespaces(*, desired: tuple, dry_run: bool, prune: bool) -> None:
    from olf import glue as glue_module

    client = glue_module.GlueClient(
        glue_module.GlueConfig(
            catalog_id=config.env("OPENLAKEFORGE_CATALOG_GLUE_CATALOG_ID"),
            region=config.env("OPENLAKEFORGE_CATALOG_GLUE_REGION"),
            catalog_name=config.env("OPENLAKEFORGE_CATALOG_NAME", "lakehouse_dev"),
        )
    )
    try:
        _reconcile_and_report("Glue", client, client.list_namespaces(), desired, dry_run=dry_run, prune=prune)
    except glue_module.GlueError as exc:
        raise typer.Exit(code=_fail(str(exc))) from exc


@catalog_app.command("sync-namespaces")
def catalog_sync_namespaces(
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the plan without changing the catalog."),
    prune: bool | None = typer.Option(
        None,
        "--prune/--no-prune",
        help="Remove managed metadata for undeclared products; object-store files are retained.",
    ),
) -> None:
    """Reconcile catalog namespaces (Polaris) or databases (Glue) with the domain descriptors.

    Phase 2 owns namespace lifecycle (ADR 0022), so this runs before any table
    is written.
    """
    from olf import catalog as catalog_module

    provider = config.env("OPENLAKEFORGE_CATALOG_PROVIDER", "polaris")
    if prune is None:
        prune = _truthy(config.env("OPENLAKEFORGE_CATALOG_PRUNE_NAMESPACES", "false"))
    desired = catalog_module.desired_namespaces(
        _repo_root(),
        silver_bucket=config.env("OPENLAKEFORGE_STORAGE_SILVER_BUCKET", "lakehouse-silver"),
        gold_bucket=config.env("OPENLAKEFORGE_STORAGE_GOLD_BUCKET", "lakehouse-gold"),
    )

    if provider == "polaris":
        log_step(f"Reconciling {len(desired)} Polaris namespace(s) from the domain descriptors...")
        _sync_polaris_namespaces(desired=desired, dry_run=dry_run, prune=prune)
    elif provider == "aws-glue":
        log_step(f"Reconciling {len(desired)} Glue database(s) from the domain descriptors...")
        _sync_glue_namespaces(desired=desired, dry_run=dry_run, prune=prune)
    else:
        raise typer.Exit(code=_fail(f"Catalog provider {provider!r} has no namespace reconciliation backend."))


@floe_app.command("render-profile")
def floe_render_profile() -> None:
    """Render the Floe EnvironmentProfile YAML for the active contract env."""
    typer.echo(floe_module.render_profile(os.environ), nl=False)


@layers_app.command("enabled")
def layers_enabled(
    layer: str = typer.Option(..., "--layer", help="Optional layer: analytics or governance."),
) -> None:
    """Exit successfully only when an optional platform layer is enabled."""
    try:
        is_enabled = layers_module.enabled(os.environ, layer)
    except ValueError as exc:
        raise typer.Exit(code=_fail(str(exc))) from exc
    if not is_enabled:
        raise typer.Exit(code=1)


@artifacts_app.command("deploy-optional-layers")
def artifacts_deploy_optional_layers() -> None:
    """Deploy artifacts for enabled optional platform layers."""
    layers_module.deploy_enabled_artifacts(
        os.environ,
        deploy_reports=deploy_superset_reports,
        deploy_metadata=deploy_openmetadata_metadata,
        report=lambda message: typer.echo(f"==> {message}"),
    )


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


@revision_app.command("compute")
def revision_compute(
    runtime_root: str = typer.Option(..., "--runtime-root", help="Rendered Floe runtime artifact root."),
) -> None:
    """Print the deterministic content revision for a rendered artifact set."""
    from olf import revision

    try:
        typer.echo(revision.compute_revision(Path(runtime_root)).revision)
    except revision.RevisionError as exc:
        raise typer.Exit(code=_fail(str(exc))) from exc


@revision_app.command("publish")
def revision_publish(
    runtime_root: str = typer.Option(..., "--runtime-root", help="Rendered Floe runtime artifact root."),
    via: str = typer.Option(
        "port-forward",
        "--via",
        help="'port-forward' for in-cluster S3-compatible storage, 'direct' for cloud S3.",
    ),
) -> None:
    """Publish a revision-qualified artifact set without activating it."""
    from olf import revision, s3

    uploads = s3.discover_runtime_artifacts(Path(runtime_root))
    if not uploads:
        raise typer.Exit(code=_fail(f"no rendered Floe runtime artifacts found under {runtime_root}."))
    bucket = _artifact_bucket()
    try:
        with _artifact_storage_client(via, bucket) as client:
            manifest = revision.publish(client, bucket, uploads)
    except revision.RevisionError as exc:
        raise typer.Exit(code=_fail(str(exc))) from exc
    typer.echo(f"Published {manifest.revision} to s3://{bucket}/{revision.revision_prefix(manifest.revision)}")


@revision_app.command("activate")
def revision_activate(
    runtime_root: str = typer.Option(..., "--runtime-root", help="Rendered Floe runtime artifact root."),
    via: str = typer.Option(
        "port-forward",
        "--via",
        help="'port-forward' for in-cluster S3-compatible storage, 'direct' for cloud S3.",
    ),
) -> None:
    """Publish and verify the immutable revision selected for a deployment."""
    from olf import revision, s3

    uploads = s3.discover_runtime_artifacts(Path(runtime_root))
    if not uploads:
        raise typer.Exit(code=_fail(f"no rendered Floe runtime artifacts found under {runtime_root}."))
    bucket = _artifact_bucket()
    try:
        with _artifact_storage_client(via, bucket) as client:
            manifest = revision.activate(client, bucket, uploads)
    except revision.RevisionError as exc:
        raise typer.Exit(code=_fail(str(exc))) from exc
    typer.echo(manifest.revision)


@revision_app.command("verify")
def revision_verify(
    revision_id: str = typer.Option(..., "--revision", help="Revision to verify, e.g. sha256:<digest>."),
    via: str = typer.Option(
        "port-forward",
        "--via",
        help="'port-forward' for in-cluster S3-compatible storage, 'direct' for cloud S3.",
    ),
) -> None:
    """Verify an immutable revision sidecar and every object it declares."""
    from olf import revision

    bucket = _artifact_bucket()
    try:
        with _artifact_storage_client(via, bucket) as client:
            manifest = revision.verify(client, bucket, revision_id)
    except revision.RevisionError as exc:
        raise typer.Exit(code=_fail(str(exc))) from exc
    typer.echo(f"Verified {manifest.revision} ({len(manifest.entries)} artifacts).")


def _artifact_bucket() -> str:
    bucket = config.env("OPENLAKEFORGE_OPS_BUCKET_NAME") or config.env("OPENLAKEFORGE_ARTIFACT_BUCKET_NAME")
    if not bucket:
        raise typer.Exit(code=_fail("no ops/artifact bucket resolved from the contract environment."))
    return bucket


@contextmanager
def _artifact_storage_client(via: str, bucket: str) -> Iterator[Any]:
    from olf import k8s, s3

    if via == "direct":
        yield boto3.client("s3", region_name=config.env("OPENLAKEFORGE_STORAGE_REGION") or None)
        return
    if via != "port-forward":
        raise typer.Exit(code=_fail(f"unknown --via mode: {via!r} (expected 'port-forward' or 'direct')."))

    namespace = config.namespace()
    secret_name = config.env("OPENLAKEFORGE_STORAGE_CREDENTIALS_SECRET_NAME")
    service = config.env("OPENLAKEFORGE_STORAGE_S3_SERVICE_NAME", "seaweedfs-s3")
    remote_port = int(config.env("OPENLAKEFORGE_STORAGE_S3_SERVICE_PORT", "8333"))
    with s3.port_forward_client(
        bucket,
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
    ) as client:
        yield client


@superset_app.command("deploy-reports")
def superset_deploy_reports() -> None:
    """Build and import source-controlled Superset report bundles."""
    deploy_superset_reports()


def deploy_superset_reports() -> None:
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
    import yaml

    from olf import superset

    repo_root = _repo_root()
    default_product = inventory_for(repo_root).default_product
    report_source_dir = config.env("SUPERSET_REPORT_SOURCE_DIR", default_product.report_source_dir)

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
            "SUPERSET_REPORT_EXPORT_BUNDLE_NAME", default_product.superset_export_bundle_name
        ),
        work_dir=Path(config.env("SUPERSET_REPORT_WORK_DIR", ".tmp/superset-reports")),
        reports_mount_path=config.env("SUPERSET_REPORTS_MOUNT_PATH", superset.REPORTS_MOUNT_PATH_DEFAULT),
        admin_username=config.env("SUPERSET_ADMIN_USERNAME", "admin"),
        dashboard_title=config.env("SUPERSET_DASHBOARD_TITLE", _default_dashboard_title()),
    )


@openmetadata_app.command("deploy-metadata")
def openmetadata_deploy_metadata() -> None:
    """Seed OpenMetadata domains, data products, and medallion containers."""
    deploy_openmetadata_metadata()


def deploy_openmetadata_metadata() -> None:
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


@k8s_app.command("set-superset-image")
def k8s_set_superset_image(
    image: str = typer.Option(..., "--image", help="Fully qualified Superset image reference."),
    timeout: str = typer.Option("600s", "--timeout", help="Timeout for the Superset deployment rollout."),
) -> None:
    """Point the Superset deployment at an image and wait for its rollout."""
    from olf import k8s

    k8s.set_superset_image(image, config.namespace(), rollout_timeout=timeout)


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


@smoke_app.command("run")
def smoke_run(
    timeout_seconds: int = typer.Option(
        2700, "--timeout-seconds", help="Hard wall-clock limit for the complete smoke path."
    ),
) -> None:
    """Deploy the slim local stack and validate one product before the deadline."""
    try:
        smoke_module.run(timeout_seconds=timeout_seconds)
    except smoke_module.SmokeError as exc:
        raise typer.Exit(code=_fail(str(exc))) from exc


@release_app.command("manifest")
def release_manifest(
    catalog: str = typer.Option(
        "release/component-catalog.yaml", "--catalog", help="Path to the component catalog."
    ),
    git_sha: str = typer.Option("", "--git-sha", help="Git commit SHA for this build (defaults to HEAD)."),
    image: list[str] | None = typer.Option(  # noqa: B008 - typer's repeatable-option pattern
        None,
        "--image",
        help="Resolved image digest as name=repo@sha256:digest. Repeatable, e.g. "
        "--image project-code=ghcr.io/malon64/openlakeforge/project-code@sha256:....",
    ),
    fmt: str = typer.Option("json", "--format", help="Output format: json or yaml."),
    output: str = typer.Option("", "--output", help="Write to this path instead of stdout."),
) -> None:
    """Emit the resolved component manifest: catalog + resolved image digests + git SHA."""
    from olf import release as release_module

    resolved_git_sha = git_sha or _git_sha()
    image_digests: dict[str, str] = {}
    for entry in image or []:
        if "=" not in entry:
            raise typer.Exit(code=_fail(f"invalid --image {entry!r}; expected name=repo@sha256:digest"))
        name, _, reference = entry.partition("=")
        image_digests[name] = reference

    try:
        catalog_data = release_module.load_catalog(catalog)
        manifest = release_module.build_manifest(catalog_data, git_sha=resolved_git_sha, image_digests=image_digests)
        rendered = release_module.render_manifest(manifest, fmt=fmt)
    except release_module.ReleaseError as exc:
        raise typer.Exit(code=_fail(str(exc))) from exc

    if output:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(rendered)
    else:
        typer.echo(rendered, nl=False)


@release_app.command("checksums")
def release_checksums(
    directory: str = typer.Option(..., "--dir", help="Directory of release assets to checksum."),
    output: str = typer.Option("", "--output", help="Write checksums.txt here (default: <dir>/checksums.txt)."),
) -> None:
    """Write a deterministic sha256 checksums.txt over every file in --dir."""
    from olf import release as release_module

    try:
        out_path = release_module.write_checksums(directory, output or None)
    except release_module.ReleaseError as exc:
        raise typer.Exit(code=_fail(str(exc))) from exc
    typer.echo(f"Wrote {out_path}")


@release_app.command("compatibility-matrix")
def release_compatibility_matrix(
    catalog: str = typer.Option(
        "release/component-catalog.yaml", "--catalog", help="Path to the component catalog."
    ),
    output: str = typer.Option("", "--output", help="Write to this path instead of stdout."),
) -> None:
    """Render the OpenLakeForge compatibility matrix from the component catalog."""
    from olf import release as release_module

    try:
        catalog_data = release_module.load_catalog(catalog)
    except release_module.ReleaseError as exc:
        raise typer.Exit(code=_fail(str(exc))) from exc
    rendered = release_module.render_compatibility_matrix(catalog_data, _repo_root())

    if output:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(rendered)
    else:
        typer.echo(rendered, nl=False)


@release_app.command("check")
def release_check(
    catalog: str = typer.Option(
        "release/component-catalog.yaml", "--catalog", help="Path to the component catalog."
    ),
    tag: str = typer.Option(
        "", "--tag", help="Release tag to validate against the catalog version (e.g. v0.1.0-alpha.1)."
    ),
) -> None:
    """Release-readiness gate: catalog/tag consistency, digest and SHA pinning, lockfiles."""
    from olf import release as release_module

    try:
        report = release_module.run_release_check(_repo_root(), catalog_path=catalog, tag=tag or None)
    except release_module.ReleaseError as exc:
        raise typer.Exit(code=_fail(str(exc))) from exc

    typer.echo(report.render())
    if not report.ok:
        raise typer.Exit(code=1)


@install_app.command("run")
def install_run(
    tag: str = typer.Option(..., "--tag", help="Release tag to install, e.g. v0.1.0-alpha.1."),
    kube_context: str = typer.Option(
        ..., "--kube-context", help="Target kubectl context; must already exist in the resolved kubeconfig."
    ),
    namespace: str = typer.Option("lakehouse", "--namespace", help="Kubernetes namespace for the platform."),
    profile: str = typer.Option("full", "--profile", help="Component profile to install: 'slim' or 'full'."),
    repo_slug: str = typer.Option(
        "malon64/openlakeforge", "--repo-slug", help="GitHub <owner>/<repo> the release was published from."
    ),
    assets_dir: str = typer.Option(
        "", "--assets-dir", help="Use already-downloaded release assets instead of fetching from GitHub."
    ),
    kubeconfig: str = typer.Option(
        "", "--kubeconfig", help="Kubeconfig path (default: $KUBECONFIG, or ~/.kube/config)."
    ),
    cluster_name: str = typer.Option(
        "openlakeforge-install", "--cluster-name", help="Cluster name recorded in the foundation contract."
    ),
    work_dir: str = typer.Option(
        "",
        "--work-dir",
        help="Directory for downloads, the unpacked bundle, and Terraform state (default: a stable "
        "~/.openlakeforge/install/<context>-<kubeconfig-hash>/<namespace> directory, reused across repeat "
        "installs -- including upgrades to a different tag -- against the same target so Terraform state "
        "survives). An explicit --work-dir is bound to the first (--kubeconfig, --kube-context, "
        "--namespace) it's used for; reusing it for a different target is refused, since the Terraform "
        "state inside it is target-scoped and reusing it elsewhere can delete or orphan resources.",
    ),
    skip_artifacts: bool = typer.Option(
        False,
        "--skip-artifacts",
        help="Apply only the static platform (Phase 1); skip Phase 2 artifacts. Also skips re-pinning "
        "project-code/Superset to their exact verified digest, which Phase 2 does -- a Phase-1-only "
        "install stays on whatever the version tag resolved to at apply time."
    ),
    skip_signature_verification: bool = typer.Option(
        False,
        "--skip-signature-verification",
        help="DANGEROUS: skip cosign verification. Only for rehearsing against an unsigned dry-run bundle "
        "(e.g. 'make release-bundle'); never use against a real release.",
    ),
    skip_digest_pin: bool = typer.Option(
        False,
        "--skip-digest-pin",
        help="Only for rehearsing against images built locally and never pushed to a registry: such an "
        "image has no registry-assigned digest to pin to. Falls Phase 2 back to patching by tag. Never "
        "use against a real release.",
    ),
    storage_values_override: str = typer.Option(
        "",
        "--storage-values-override",
        help="Helm values file layered on top of the bundle's SeaweedFS values (e.g. to switch data "
        "volumes off emptyDir onto a PersistentVolumeClaim). Its path must be OUTSIDE --work-dir's "
        "bundle/ subtree when first passed -- that subtree is deleted and re-extracted on every "
        "install/upgrade. Only needs to be passed once per --work-dir: its content is copied into "
        "--work-dir and reused automatically by every later run against the same target, even one "
        "that omits this flag.",
    ),
    adopt_namespace: bool = typer.Option(
        False,
        "--adopt-namespace",
        help="Take ownership of a pre-existing --namespace this --work-dir has no record of managing. "
        "Without this, olf install refuses (rather than silently adopting or resetting) a namespace "
        "that already exists but isn't already tracked in this work directory's Terraform state -- it "
        "may be unrelated to this install. Only needed once per --work-dir; a namespace already owned "
        "by a prior run of this exact target is reconciled normally regardless of this flag.",
    ),
) -> None:
    """Verify and install a tagged release into an existing cluster -- no repository clone."""
    from olf import install as install_module

    options = install_module.InstallOptions(
        tag=tag,
        kube_context=kube_context,
        repo_slug=repo_slug,
        namespace=namespace,
        profile=profile,
        cluster_name=cluster_name,
        kubeconfig_path=kubeconfig or None,
        assets_dir=Path(assets_dir) if assets_dir else None,
        work_dir=Path(work_dir) if work_dir else None,
        skip_artifacts=skip_artifacts,
        skip_signature_verification=skip_signature_verification,
        skip_digest_pin=skip_digest_pin,
        storage_values_override=Path(storage_values_override) if storage_values_override else None,
        adopt_namespace=adopt_namespace,
    )
    try:
        result = install_module.run_install(options)
    except install_module.InstallError as exc:
        raise typer.Exit(code=_fail(str(exc))) from exc
    typer.echo(f"Installed {tag} into context {kube_context}, namespace {namespace}.")
    # The RESOLVED absolute path, not the raw --kubeconfig value: a relative
    # value normalized correctly for this run (against this cwd), but the
    # advertised command may be copy-pasted and run from a different
    # directory later, where the same relative value would resolve to a
    # different file (or none).
    resolved_kubeconfig_path = result["resolved_kubeconfig_path"]
    typer.echo(
        f"Manifest at {result['manifest_path']} -- run 'olf install verify --manifest {result['manifest_path']} "
        f"--kube-context {kube_context} --namespace {namespace} --kubeconfig {resolved_kubeconfig_path} "
        f"--profile {profile}' to prove the running images match it."
    )


@install_app.command("verify")
def install_verify(
    manifest: str = typer.Option(..., "--manifest", help="Path to component-manifest.json."),
    namespace: str = typer.Option("lakehouse", "--namespace", help="Kubernetes namespace to inspect."),
    kube_context: str = typer.Option(..., "--kube-context", help="kubectl context to inspect."),
    kubeconfig: str = typer.Option(
        "",
        "--kubeconfig",
        help="Kubeconfig path (default: $KUBECONFIG, or ~/.kube/config). Pass the same value given to "
        "'olf install run' if that context only exists in a non-default kubeconfig.",
    ),
    profile: str = typer.Option("full", "--profile", help="Component profile that was installed: 'slim' or 'full'."),
    strict: bool = typer.Option(
        False, "--strict", help="Also fail on running images the manifest does not declare."
    ),
    skip_image: list[str] | None = typer.Option(  # noqa: B008 - typer's repeatable-option pattern
        None,
        "--skip-image",
        help="Canonical repository (e.g. ghcr.io/openlakeforge/project-code) to exclude from the parity "
        "check. Repeatable. Only for rehearsing against images built locally and never pushed to a "
        "registry -- there is no correct expected digest to check them against. Never use against a "
        "real install.",
    ),
) -> None:
    """Prove the running cluster's image digests match the release manifest exactly."""
    from olf import install as install_module

    try:
        install_module.profile_env(profile)
    except install_module.InstallError as exc:
        raise typer.Exit(code=_fail(str(exc))) from exc

    manifest_data = json.loads(Path(manifest).read_text())
    governance = profile == "full"
    analytics = profile == "full"
    report = install_module.run_verify(
        manifest_data,
        namespace=namespace,
        kube_context=kube_context,
        governance=governance,
        analytics=analytics,
        kubeconfig_path=kubeconfig or None,
        skip_repositories=frozenset(skip_image or []),
    )
    typer.echo(report.render())
    if not report.ok(strict=strict):
        raise typer.Exit(code=1)


def _git_sha() -> str:
    import subprocess

    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, cwd=_repo_root()
        )
        return result.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


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
