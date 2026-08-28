"""Commands for validating, building, and verifying writable OpenLakeForge data projects."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import typer

from olf.commands._shared import fail
from olf.project import ProjectSpec, validate_project

app = typer.Typer(help="Validate and build writable OpenLakeForge data projects.")

revision_app = typer.Typer(help="Inspect and verify a published ProjectRevision (#154).")
app.add_typer(revision_app, name="revision")


@app.command("validate")
def validate(
    project: str = typer.Option(..., "--project", help="Explicit writable project root."),
) -> None:
    """Emit a machine-readable report for one project root."""
    from olf.distribution import runtime_layout

    layout = runtime_layout()
    report = validate_project(ProjectSpec(root=Path(project), distribution_root=layout.distribution_root))
    typer.echo(report.render_json())
    if not report.ok:
        raise typer.Exit(code=1)


@app.command("build")
def build(
    project: str = typer.Option(..., "--project", help="Explicit writable project root."),
    image: str = typer.Option(
        ..., "--image", help="project-code image reference; digest-pinned, or resolved to a digest via Docker."
    ),
    output: str = typer.Option(
        "", "--output", help="Publish into this local directory instead of the ops bucket."
    ),
    via: str = typer.Option(
        "port-forward",
        "--via",
        help="'port-forward' for in-cluster S3-compatible storage, 'direct' for cloud S3. Ignored with --output.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print the full manifest instead of the revision id."),
) -> None:
    """Build and publish the immutable ProjectRevision for one writable project."""
    from olf.artifact_store import ArtifactStoreError
    from olf.commands._project import writable_project_layout
    from olf.project_revision import ProjectRevisionError, build_project_revision, publish

    layout = writable_project_layout(project)
    spec = ProjectSpec(root=layout.project_root, distribution_root=layout.distribution_root)
    try:
        manifest = build_project_revision(spec, image=image, distribution_version=layout.distribution_version)
        with _revision_store(via=via, output=output) as store:
            publish(store, manifest, spec)
    except (ProjectRevisionError, ArtifactStoreError) as exc:
        raise typer.Exit(code=fail(str(exc))) from exc
    typer.echo(manifest.to_json() if json_output else manifest.revision)


@revision_app.command("inspect")
def revision_inspect(
    revision: str = typer.Option(..., "--revision", help="Revision to inspect, e.g. sha256:<digest>."),
    output: str = typer.Option("", "--output", help="Read from this local directory instead of the ops bucket."),
    via: str = typer.Option(
        "port-forward",
        "--via",
        help="'port-forward' for in-cluster S3-compatible storage, 'direct' for cloud S3. Ignored with --output.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print the full manifest instead of a summary."),
) -> None:
    """Read a published ProjectRevision manifest without rebuilding source."""
    from olf.artifact_store import ArtifactStoreError
    from olf.project_revision import ProjectRevisionError
    from olf.project_revision import inspect as inspect_revision

    try:
        with _revision_store(via=via, output=output) as store:
            manifest = inspect_revision(store, revision)
    except (ProjectRevisionError, ArtifactStoreError) as exc:
        raise typer.Exit(code=fail(str(exc))) from exc
    if json_output:
        typer.echo(manifest.to_json())
        return
    counts = ", ".join(f"{component.name}={len(component.entries)}" for component in manifest.components)
    typer.echo(f"{manifest.revision} [{manifest.project_name}] {counts}")


@revision_app.command("verify")
def revision_verify(
    revision: str = typer.Option(..., "--revision", help="Revision to verify, e.g. sha256:<digest>."),
    output: str = typer.Option("", "--output", help="Read from this local directory instead of the ops bucket."),
    via: str = typer.Option(
        "port-forward",
        "--via",
        help="'port-forward' for in-cluster S3-compatible storage, 'direct' for cloud S3. Ignored with --output.",
    ),
    skip_compatibility_check: bool = typer.Option(
        False, "--skip-compatibility-check", help="Verify content only; do not gate on the running distribution."
    ),
) -> None:
    """Verify manifest self-consistency, distribution compatibility, and every published object's digest."""
    from olf.artifact_store import ArtifactStoreError
    from olf.distribution import runtime_layout
    from olf.project_revision import ProjectRevisionError
    from olf.project_revision import verify as verify_revision

    running_version = None if skip_compatibility_check else runtime_layout().distribution_version
    try:
        with _revision_store(via=via, output=output) as store:
            manifest = verify_revision(store, revision, running_distribution_version=running_version)
    except (ProjectRevisionError, ArtifactStoreError) as exc:
        raise typer.Exit(code=fail(str(exc))) from exc
    entry_count = sum(len(component.entries) for component in manifest.components)
    typer.echo(f"Verified {manifest.revision} ({entry_count} entries across {len(manifest.components)} components).")


@contextmanager
def _revision_store(*, via: str, output: str) -> Iterator[object]:
    from olf.artifact_store import FilesystemRevisionStore, S3RevisionStore, artifact_bucket, artifact_storage_client

    if output:
        yield FilesystemRevisionStore(Path(output))
        return
    bucket = artifact_bucket()
    with artifact_storage_client(via, bucket) as client:
        yield S3RevisionStore(client, bucket)
