"""The olf CLI: entrypoint for OpenLakeForge deployment tooling.

Command groups live in `olf.commands.*`; this module only constructs the
root Typer application, registers each group, and wires `main()`.
"""

from __future__ import annotations

import typer

import olf
from olf.commands import (
    artifacts,
    catalog,
    contracts,
    deployment,
    domain,
    e2e,
    floe,
    k8s,
    layers,
    openmetadata,
    product,
    release,
    revision,
    smoke,
    source,
    superset,
)

app = typer.Typer(
    name="olf",
    help="OpenLakeForge deployment tooling.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)

app.add_typer(contracts.app, name="contracts")
app.add_typer(catalog.app, name="catalog")
app.add_typer(floe.app, name="floe")
app.add_typer(artifacts.app, name="artifacts")
app.add_typer(layers.app, name="layers")
app.add_typer(revision.app, name="revision")
app.add_typer(superset.app, name="superset")
app.add_typer(openmetadata.app, name="openmetadata")
app.add_typer(k8s.app, name="k8s")
app.add_typer(e2e.app, name="e2e")
app.add_typer(smoke.app, name="smoke")
app.add_typer(release.app, name="release")
app.add_typer(source.app, name="source")
app.add_typer(domain.app, name="domain")
app.add_typer(product.app, name="product")

app.command("deploy")(deployment.deploy)
app.command("destroy")(deployment.destroy)
app.command("status")(deployment.status)
app.command("forward")(deployment.forward)


@app.callback()
def _root() -> None:
    """OpenLakeForge deployment tooling."""


@app.command()
def version() -> None:
    """Print the tooling version."""
    typer.echo(olf.__version__)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
