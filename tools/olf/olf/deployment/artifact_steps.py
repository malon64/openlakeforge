"""Provider-neutral dynamic artifact deployment steps.

Port of the shared portion of `scripts/{local,aws,azure}/stack/
deploy-artifacts.sh`: catalog namespace reconciliation, immutable Floe
revision activation, legacy manifest upload, and optional Superset/
OpenMetadata layer deployment. Parameterized by `via` ("port-forward" for
the in-cluster S3-compatible local/Azure path, "direct" for AWS's own S3)
so every provider shares one implementation - kept out of
`olf.deployment.local` so the AWS/Azure providers (#125) reuse it
unmodified. Reuses `olf.artifact_store.artifact_storage_client` rather
than keeping a second port-forward-only client builder.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import typer

from olf import layers, log, revision, s3
from olf.artifact_store import ArtifactStoreError, artifact_bucket, artifact_storage_client
from olf.deployment.errors import DeploymentPreconditionError


class ArtifactOperationError(DeploymentPreconditionError):
    """Raised when a called `olf` artifact operation fails."""


def _run_cli(description: str, fn: Any, *args: Any, **kwargs: Any) -> None:
    try:
        fn(*args, **kwargs)
    except typer.Exit as exc:
        raise ArtifactOperationError(f"{description} failed (see the ERROR above).") from exc


def sync_catalog_namespaces() -> None:
    # sync_namespaces, not the catalog_sync_namespaces typer command: this
    # already runs inside the deploy flow's own hydrated contract
    # environment (see local/artifacts.py, cloud/artifacts.py) - resolving
    # a second one here would be redundant and could pick a different
    # provider/project selection than the one already active.
    from olf.commands.catalog import sync_namespaces

    _run_cli("Catalog namespace reconciliation", sync_namespaces, dry_run=False, prune=None)


def activate_runtime_revision(runtime_root: Path, *, via: str = "port-forward") -> str:
    uploads = s3.discover_runtime_artifacts(runtime_root)
    if not uploads:
        raise ArtifactOperationError(f"no rendered Floe runtime artifacts found under {runtime_root}.")
    try:
        bucket = artifact_bucket()
        with artifact_storage_client(via, bucket) as client:
            manifest = revision.activate(client, bucket, uploads)
    except (ArtifactStoreError, revision.RevisionError) as exc:
        raise ArtifactOperationError(str(exc)) from exc
    return manifest.revision


def upload_runtime_manifests(runtime_root: Path, *, via: str = "port-forward") -> None:
    from olf.commands.artifacts import upload_manifests

    _run_cli(
        "Legacy Floe manifest upload",
        upload_manifests,
        via=via,
        manifest_root="",
        runtime_root=str(runtime_root),
    )


def deploy_optional_layer_artifacts(environ: Mapping[str, str]) -> None:
    from olf.commands.openmetadata import deploy_openmetadata_metadata
    from olf.commands.superset import deploy_superset_reports

    layers.deploy_enabled_artifacts(
        environ,
        deploy_reports=deploy_superset_reports,
        deploy_metadata=deploy_openmetadata_metadata,
        report=log.step,
    )
