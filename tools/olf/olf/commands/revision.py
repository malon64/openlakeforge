"""Immutable Floe runtime-artifact revision helpers."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import typer

from olf import config
from olf.commands._shared import fail

app = typer.Typer(help="Immutable Floe runtime-artifact revision helpers.")


@app.command("compute")
def revision_compute(
    runtime_root: str = typer.Option(..., "--runtime-root", help="Rendered Floe runtime artifact root."),
) -> None:
    """Print the deterministic content revision for a rendered artifact set."""
    from olf import revision

    try:
        typer.echo(revision.compute_revision(Path(runtime_root)).revision)
    except revision.RevisionError as exc:
        raise typer.Exit(code=fail(str(exc))) from exc


@app.command("publish")
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
        raise typer.Exit(code=fail(f"no rendered Floe runtime artifacts found under {runtime_root}."))
    bucket = _artifact_bucket()
    try:
        with _artifact_storage_client(via, bucket) as client:
            manifest = revision.publish(client, bucket, uploads)
    except revision.RevisionError as exc:
        raise typer.Exit(code=fail(str(exc))) from exc
    typer.echo(f"Published {manifest.revision} to s3://{bucket}/{revision.revision_prefix(manifest.revision)}")


@app.command("activate")
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
        raise typer.Exit(code=fail(f"no rendered Floe runtime artifacts found under {runtime_root}."))
    bucket = _artifact_bucket()
    try:
        with _artifact_storage_client(via, bucket) as client:
            manifest = revision.activate(client, bucket, uploads)
    except revision.RevisionError as exc:
        raise typer.Exit(code=fail(str(exc))) from exc
    typer.echo(manifest.revision)


@app.command("verify")
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
        raise typer.Exit(code=fail(str(exc))) from exc
    typer.echo(f"Verified {manifest.revision} ({len(manifest.entries)} artifacts).")


def _artifact_bucket() -> str:
    bucket = config.env("OPENLAKEFORGE_OPS_BUCKET_NAME") or config.env("OPENLAKEFORGE_ARTIFACT_BUCKET_NAME")
    if not bucket:
        raise typer.Exit(code=fail("no ops/artifact bucket resolved from the contract environment."))
    return bucket


@contextmanager
def _artifact_storage_client(via: str, bucket: str) -> Iterator[Any]:
    from olf import k8s, s3

    if via == "direct":
        from olf.auth import aws_session

        region = config.env("OPENLAKEFORGE_STORAGE_REGION") or None
        yield aws_session(os.environ, region=region).client("s3", region_name=region)
        return
    if via != "port-forward":
        raise typer.Exit(code=fail(f"unknown --via mode: {via!r} (expected 'port-forward' or 'direct')."))

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
