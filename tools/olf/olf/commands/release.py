"""Release manifest, checksums, compatibility matrix, and readiness gate."""

from __future__ import annotations

from pathlib import Path

import typer

from olf import config
from olf.commands._shared import fail

app = typer.Typer(help="Release manifest, checksums, compatibility matrix, and readiness gate.")


@app.command("manifest")
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
            raise typer.Exit(code=fail(f"invalid --image {entry!r}; expected name=repo@sha256:digest"))
        name, _, reference = entry.partition("=")
        image_digests[name] = reference

    try:
        catalog_data = release_module.load_catalog(catalog)
        manifest = release_module.build_manifest(catalog_data, git_sha=resolved_git_sha, image_digests=image_digests)
        rendered = release_module.render_manifest(manifest, fmt=fmt)
    except release_module.ReleaseError as exc:
        raise typer.Exit(code=fail(str(exc))) from exc

    if output:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(rendered)
    else:
        typer.echo(rendered, nl=False)


@app.command("checksums")
def release_checksums(
    directory: str = typer.Option(..., "--dir", help="Directory of release assets to checksum."),
    output: str = typer.Option("", "--output", help="Write checksums.txt here (default: <dir>/checksums.txt)."),
) -> None:
    """Write a deterministic sha256 checksums.txt over every file in --dir."""
    from olf import release as release_module

    try:
        out_path = release_module.write_checksums(directory, output or None)
    except release_module.ReleaseError as exc:
        raise typer.Exit(code=fail(str(exc))) from exc
    typer.echo(f"Wrote {out_path}")


@app.command("compatibility-matrix")
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
        raise typer.Exit(code=fail(str(exc))) from exc
    rendered = release_module.render_compatibility_matrix(catalog_data, config.repo_root())

    if output:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(rendered)
    else:
        typer.echo(rendered, nl=False)


@app.command("check")
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
        report = release_module.run_release_check(config.repo_root(), catalog_path=catalog, tag=tag or None)
    except release_module.ReleaseError as exc:
        raise typer.Exit(code=fail(str(exc))) from exc

    typer.echo(report.render())
    if not report.ok:
        raise typer.Exit(code=1)


def _git_sha() -> str:
    import subprocess

    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, cwd=config.repo_root()
        )
        return result.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
