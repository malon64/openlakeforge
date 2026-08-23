"""Provider-neutral dynamic artifact deployment steps.

Port of the shared portion of `scripts/{local,aws,azure}/stack/
deploy-artifacts.sh`: catalog namespace reconciliation, immutable Floe
revision activation, legacy manifest upload, and optional Superset/
OpenMetadata layer deployment. Parameterized by `via` ("port-forward" for
the in-cluster S3-compatible local/Azure path, "direct" for AWS's own S3)
so every provider shares one implementation - kept out of
`olf.deployment.local` so the AWS/Azure providers (#125) reuse it
unmodified. Reuses `olf.commands.revision._artifact_storage_client` rather
than keeping a second port-forward-only client builder.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import typer

from olf import config as olf_config
from olf import layers, log, revision, s3
from olf.deployment.errors import DeploymentPreconditionError


class ArtifactOperationError(DeploymentPreconditionError):
    """Raised when a called `olf` artifact operation fails."""


def _run_cli(description: str, fn: Any, *args: Any, **kwargs: Any) -> None:
    try:
        fn(*args, **kwargs)
    except typer.Exit as exc:
        raise ArtifactOperationError(f"{description} failed (see the ERROR above).") from exc


def sync_catalog_namespaces() -> None:
    from olf.commands.catalog import catalog_sync_namespaces

    _run_cli("Catalog namespace reconciliation", catalog_sync_namespaces, dry_run=False, prune=None)


def _artifact_bucket() -> str:
    bucket = olf_config.env("OPENLAKEFORGE_OPS_BUCKET_NAME") or olf_config.env("OPENLAKEFORGE_ARTIFACT_BUCKET_NAME")
    if not bucket:
        raise ArtifactOperationError("no ops/artifact bucket resolved from the contract environment.")
    return bucket


def activate_runtime_revision(runtime_root: Path, *, via: str = "port-forward") -> str:
    from olf.commands.revision import _artifact_storage_client

    uploads = s3.discover_runtime_artifacts(runtime_root)
    if not uploads:
        raise ArtifactOperationError(f"no rendered Floe runtime artifacts found under {runtime_root}.")
    bucket = _artifact_bucket()
    try:
        with _artifact_storage_client(via, bucket) as client:
            manifest = revision.activate(client, bucket, uploads)
    except revision.RevisionError as exc:
        raise ArtifactOperationError(str(exc)) from exc
    return manifest.revision


def upload_runtime_manifests(runtime_root: Path, *, via: str = "port-forward") -> None:
    from olf.commands.artifacts import artifacts_upload_manifests

    _run_cli(
        "Legacy Floe manifest upload",
        artifacts_upload_manifests,
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
