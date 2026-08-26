"""Shared CLI helpers for command groups."""

from __future__ import annotations

import os
from pathlib import Path

import typer

from olf import log


def log_step(message: str) -> None:
    """Announce a CLI step."""
    log.step(message)


def fail(message: str) -> int:
    """Log error and return exit code 1."""
    log.error(message)
    return 1


def deployment_context(
    provider: str, *, profile: str, namespace: str, cluster_name: str, kubeconfig_path: str = "", project_root: str = ""
):  # noqa: ANN202
    """Resolve the installed-vs-source runtime layout into a `DeploymentContext`.

    Shared by every command that needs provider paths - `olf deploy`/`plan`/
    `doctor`/`destroy`/`status`/`forward` (`olf.commands.deployment`) and
    `olf e2e run` (`olf.commands.e2e`). A second, independent resolution here
    would drift: an installed distribution's Terraform roots, kubeconfig, and
    work/state/cache paths must resolve identically for every command, or
    `olf e2e run` ends up validating a different deployment than `olf deploy`
    just created.
    """
    from olf.deployment.context import DeploymentContext, Profile, Provider
    from olf.distribution import DistributionError, runtime_layout

    try:
        resolved_provider = Provider(provider)
    except ValueError as exc:
        raise typer.Exit(code=fail(f"unknown --provider: {provider!r}")) from exc
    try:
        resolved_profile = Profile(profile)
    except ValueError as exc:
        raise typer.Exit(code=fail(f"unknown --profile: {profile!r} (expected 'full' or 'slim')")) from exc

    layout_env = dict(os.environ)
    if project_root:
        layout_env["OPENLAKEFORGE_PROJECT_ROOT"] = project_root
    try:
        layout = runtime_layout(layout_env)
    except DistributionError as exc:
        raise typer.Exit(code=fail(str(exc))) from exc
    kwargs: dict[str, object] = {
        "repo_root": layout.project_root,
        "distribution_root": layout.distribution_root,
        "state_root": None if layout.is_source else layout.state_root,
        "work_root": None if layout.is_source else layout.work_root,
        "cache_root": None if layout.is_source else layout.cache_root,
        "profile": resolved_profile,
    }
    if namespace:
        kwargs["namespace"] = namespace
    if resolved_provider == Provider.LOCAL and cluster_name:
        kwargs["cluster_name"] = cluster_name
    if kubeconfig_path:
        kwargs["kubeconfig_path"] = Path(kubeconfig_path)
    return DeploymentContext.for_provider(resolved_provider, **kwargs)
