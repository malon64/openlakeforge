"""Floe profile and manifest helpers."""

from __future__ import annotations

import os
from pathlib import Path

import typer

from olf import floe as floe_module
from olf.commands._shared import fail

app = typer.Typer(help="Floe profile and manifest helpers.")

revision_app = typer.Typer(help="Immutable Floe runtime-artifact revision helpers.")
app.add_typer(revision_app, name="revision")


@app.command("render-profile")
def floe_render_profile() -> None:
    """Render the Floe EnvironmentProfile YAML for the active contract env."""
    typer.echo(floe_module.render_profile(os.environ), nl=False)


@app.command("generate-manifests")
def generate_manifests(
    provider: str = typer.Option("local", "--provider", help="local, aws, or azure."),
    profile: str = typer.Option("", "--profile", help="Deprecated single-DEV preset shorthand: 'full' or 'slim'."),
    namespace: str = typer.Option("", "--namespace", help="Kubernetes namespace override."),
    cluster_name: str = typer.Option("", "--cluster-name", help="Local kind cluster name override."),
    kubeconfig_path: str = typer.Option("", "--kubeconfig-path", help="Kubeconfig file path override."),
    project_root: str = typer.Option(
        "", "--project-root", help="Writable project root; defaults to the current directory."
    ),
) -> None:
    """Generate Floe manifests using resolved provider contracts, never shell exports."""
    from olf.commands.deployment import _build_context, _build_engine
    from olf.commands.runtime import _contract_terraform_dir
    from olf.deployment import contract_env
    from olf.deployment.errors import DeploymentError
    from olf.deployment.floe_manifests import generate_local_manifests
    from olf.deployment.local.artifacts import applied_contract_environment
    from olf.deployment.local.provider import LocalProvider

    try:
        context = _build_context(
            provider,
            profile=profile,
            namespace=namespace,
            cluster_name=cluster_name,
            kubeconfig_path=kubeconfig_path,
            project_root=project_root,
        )
        engine = _build_engine(context, var_file="")
        deployment_provider = engine.provider
        if isinstance(deployment_provider, LocalProvider):
            # environ=deployment_provider.env: an installed distribution's
            # state/data roots live under OLF_HOME, not next to the
            # contract Terraform dir - without this, the contract read
            # silently falls back to bare os.environ and always resolves
            # to "no contracts yet", even after a real platform apply.
            with applied_contract_environment(
                deployment_provider.config,
                contract_terraform_dir=_contract_terraform_dir(context.paths.platform_terraform_dir),
                environ=deployment_provider.env,
            ) as contract_environ:
                generate_local_manifests(
                    deployment_provider.config.floe,
                    deployment_provider.tools,
                    repo_root=context.paths.repo_root,
                    distribution_root=context.paths.distribution_root,
                    namespace=context.namespace,
                    governance_enabled=context.features.governance_enabled,
                    environ=contract_environ,
                    env=deployment_provider.env,
                )
        else:
            facts = deployment_provider._foundation_facts  # noqa: SLF001 - provider resolves cloud context once.
            with contract_env.applied_contract_environment(
                contract_terraform_dir=_contract_terraform_dir(context.paths.platform_terraform_dir),
                repo_root=context.paths.repo_root,
                namespace=context.namespace,
                kube_context=facts.kube_context,
                kubeconfig_path=context.paths.kubeconfig_path,
                port_forward_log_prefix=context.paths.port_forward_log_prefix,
                environ=deployment_provider.env,
            ) as contract_environ:
                deployment_provider.backend.generate_floe_manifests(
                    deployment_provider.config,
                    deployment_provider.tools,
                    repo_root=context.paths.repo_root,
                    distribution_root=context.paths.distribution_root,
                    namespace=context.namespace,
                    governance_enabled=context.features.governance_enabled,
                    environ=contract_environ,
                    env=deployment_provider.env,
                )
    except DeploymentError as exc:
        raise typer.Exit(code=fail(str(exc))) from exc


@revision_app.command("compute")
def revision_compute(
    runtime_root: str = typer.Option(..., "--runtime-root", help="Rendered Floe runtime artifact root."),
) -> None:
    """Print the deterministic content revision for a rendered artifact set."""
    from olf import revision

    try:
        typer.echo(revision.compute_revision(Path(runtime_root)).revision)
    except revision.RevisionError as exc:
        raise typer.Exit(code=fail(str(exc))) from exc


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
    from olf.artifact_store import ArtifactStoreError, artifact_bucket, artifact_storage_client

    uploads = s3.discover_runtime_artifacts(Path(runtime_root))
    if not uploads:
        raise typer.Exit(code=fail(f"no rendered Floe runtime artifacts found under {runtime_root}."))
    try:
        bucket = artifact_bucket()
        with artifact_storage_client(via, bucket) as client:
            manifest = revision.publish(client, bucket, uploads)
    except (revision.RevisionError, ArtifactStoreError) as exc:
        raise typer.Exit(code=fail(str(exc))) from exc
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
    from olf.artifact_store import ArtifactStoreError, artifact_bucket, artifact_storage_client

    uploads = s3.discover_runtime_artifacts(Path(runtime_root))
    if not uploads:
        raise typer.Exit(code=fail(f"no rendered Floe runtime artifacts found under {runtime_root}."))
    try:
        bucket = artifact_bucket()
        with artifact_storage_client(via, bucket) as client:
            manifest = revision.activate(client, bucket, uploads)
    except (revision.RevisionError, ArtifactStoreError) as exc:
        raise typer.Exit(code=fail(str(exc))) from exc
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
    from olf.artifact_store import ArtifactStoreError, artifact_bucket, artifact_storage_client

    try:
        bucket = artifact_bucket()
        with artifact_storage_client(via, bucket) as client:
            manifest = revision.verify(client, bucket, revision_id)
    except (revision.RevisionError, ArtifactStoreError) as exc:
        raise typer.Exit(code=fail(str(exc))) from exc
    typer.echo(f"Verified {manifest.revision} ({len(manifest.entries)} artifacts).")
