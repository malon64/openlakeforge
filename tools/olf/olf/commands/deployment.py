"""Thin `olf deploy`/`destroy`/`status`/`forward` commands.

Each command builds a `DeploymentContext` + `DeploymentEngine` and delegates
immediately; all lifecycle logic lives in `olf.deployment.*`. The `--phase`
option is what lets the granular Make target delegates (`local-foundation-up`,
`local-platform-up`, ...) share this exact code path instead of duplicating
lifecycle logic.
"""

from __future__ import annotations

import os
from pathlib import Path

import typer

from olf.commands._shared import fail

app_help = "Deployment lifecycle: foundation, platform, and artifact orchestration."


def _build_context(
    provider: str, *, profile: str, namespace: str, cluster_name: str, kubeconfig_path: str = "", project_root: str = ""
):  # noqa: ANN202
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


def _build_engine(context, *, var_file: str):  # noqa: ANN001, ANN202
    from olf.deployment.engine import DeploymentEngine, Toolkit, build_provider
    from olf.deployment.errors import DeploymentError

    try:
        tooling_env = {**os.environ, "OLF_DISTRIBUTION_ROOT": str(context.paths.distribution_root)}
        provider = build_provider(
            context,
            toolkit=Toolkit.default(environ=tooling_env),
            environ=os.environ,
            var_file=Path(var_file) if var_file else None,
        )
    except DeploymentError as exc:
        raise typer.Exit(code=fail(str(exc))) from exc
    return DeploymentEngine(provider)


def _resolve_phase(phase: str, *, valid: tuple[str, ...]):  # noqa: ANN202
    from olf.deployment.engine import DeploymentPhase

    if phase not in valid:
        raise typer.Exit(code=fail(f"unknown --phase: {phase!r} (expected one of {', '.join(valid)})"))
    return DeploymentPhase(phase)


def deploy(
    provider: str = typer.Option("local", "--provider", help="Target deployment provider."),
    profile: str = typer.Option("full", "--profile", help="'full' or 'slim'."),
    phase: str = typer.Option(
        "all", "--phase", help="'all', 'foundation', 'prefetch', 'platform', or 'artifacts'."
    ),
    namespace: str = typer.Option("", "--namespace", help="Kubernetes namespace override."),
    cluster_name: str = typer.Option("", "--cluster-name", help="Local kind cluster name override."),
    kubeconfig_path: str = typer.Option("", "--kubeconfig-path", help="Local kubeconfig file path override."),
    project_root: str = typer.Option("", "--project-root", help="Writable project root; defaults to the bundled demo."),
    var_file: str = typer.Option("", "--var-file", help="Terraform tfvars file override."),
) -> None:
    """Deploy a provider's lifecycle, or a single phase of it."""
    from olf.deployment.errors import DeploymentError

    context = _build_context(
        provider,
        profile=profile,
        namespace=namespace,
        cluster_name=cluster_name,
        kubeconfig_path=kubeconfig_path,
        project_root=project_root,
    )
    engine = _build_engine(context, var_file=var_file)
    resolved_phase = _resolve_phase(phase, valid=("all", "foundation", "prefetch", "platform", "artifacts"))
    try:
        engine.deploy(resolved_phase)
    except DeploymentError as exc:
        raise typer.Exit(code=fail(str(exc))) from exc


def plan(
    provider: str = typer.Option("local", "--provider", help="Target deployment provider."),
    profile: str = typer.Option("full", "--profile", help="'full' or 'slim'."),
    phase: str = typer.Option("all", "--phase", help="'all', 'foundation', or 'platform'."),
    namespace: str = typer.Option("", "--namespace", help="Kubernetes namespace override."),
    cluster_name: str = typer.Option("", "--cluster-name", help="Local kind cluster name override."),
    kubeconfig_path: str = typer.Option("", "--kubeconfig-path", help="Kubeconfig file path override."),
    project_root: str = typer.Option("", "--project-root", help="Writable project root; defaults to the bundled demo."),
    var_file: str = typer.Option("", "--var-file", help="Terraform tfvars file override."),
    detailed_exitcode: bool = typer.Option(False, "--detailed-exitcode", help="Return 2 when changes are pending."),
) -> None:
    """Plan Terraform-managed deployment phases without applying them."""
    from olf.deployment.errors import DeploymentError

    context = _build_context(
        provider,
        profile=profile,
        namespace=namespace,
        cluster_name=cluster_name,
        kubeconfig_path=kubeconfig_path,
        project_root=project_root,
    )
    engine = _build_engine(context, var_file=var_file)
    resolved_phase = _resolve_phase(phase, valid=("all", "foundation", "platform"))
    try:
        changes = engine.plan(resolved_phase)
    except DeploymentError as exc:
        raise typer.Exit(code=fail(str(exc))) from exc
    typer.echo("Terraform changes are pending." if changes else "Terraform reports no changes.")
    if changes and detailed_exitcode:
        raise typer.Exit(code=2)


def doctor(
    provider: str = typer.Option("local", "--provider", help="Target deployment provider."),
    profile: str = typer.Option("full", "--profile", help="'full' or 'slim'."),
    phase: str = typer.Option("all", "--phase", help="'all', 'foundation', 'platform', or 'artifacts'."),
    namespace: str = typer.Option("", "--namespace", help="Kubernetes namespace override."),
    cluster_name: str = typer.Option("", "--cluster-name", help="Local kind cluster name override."),
    kubeconfig_path: str = typer.Option("", "--kubeconfig-path", help="Kubeconfig file path override."),
    project_root: str = typer.Option("", "--project-root", help="Writable project root; defaults to the bundled demo."),
    var_file: str = typer.Option("", "--var-file", help="Terraform tfvars file override."),
) -> None:
    """Check host tools, source inputs, and provider authentication without mutating deployment state."""
    from olf.deployment.errors import DeploymentError

    context = _build_context(
        provider,
        profile=profile,
        namespace=namespace,
        cluster_name=cluster_name,
        kubeconfig_path=kubeconfig_path,
        project_root=project_root,
    )
    engine = _build_engine(context, var_file=var_file)
    resolved_phase = _resolve_phase(phase, valid=("all", "foundation", "platform", "artifacts"))
    try:
        report = engine.doctor(resolved_phase)
    except DeploymentError as exc:
        raise typer.Exit(code=fail(str(exc))) from exc
    typer.echo(report.render())
    if not report.ok:
        raise typer.Exit(code=1)


def destroy(
    provider: str = typer.Option("local", "--provider", help="Target deployment provider."),
    profile: str = typer.Option("full", "--profile", help="'full' or 'slim'."),
    phase: str = typer.Option("all", "--phase", help="'all', 'platform', or 'foundation'."),
    namespace: str = typer.Option("", "--namespace", help="Kubernetes namespace override."),
    cluster_name: str = typer.Option("", "--cluster-name", help="Local kind cluster name override."),
    kubeconfig_path: str = typer.Option("", "--kubeconfig-path", help="Local kubeconfig file path override."),
    project_root: str = typer.Option("", "--project-root", help="Writable project root; defaults to the bundled demo."),
    var_file: str = typer.Option("", "--var-file", help="Terraform tfvars file override."),
    force: bool = typer.Option(
        False, "--force", help="Destroy the foundation even if platform resources remain."
    ),
) -> None:
    """Tear down a provider's lifecycle, or a single phase of it."""
    from olf.deployment.errors import DeploymentError

    context = _build_context(
        provider,
        profile=profile,
        namespace=namespace,
        cluster_name=cluster_name,
        kubeconfig_path=kubeconfig_path,
        project_root=project_root,
    )
    engine = _build_engine(context, var_file=var_file)
    resolved_phase = _resolve_phase(phase, valid=("all", "platform", "foundation"))
    try:
        engine.destroy(resolved_phase, force=force)
    except DeploymentError as exc:
        raise typer.Exit(code=fail(str(exc))) from exc


def status(
    provider: str = typer.Option("local", "--provider", help="Target deployment provider."),
    namespace: str = typer.Option("", "--namespace", help="Kubernetes namespace override."),
    cluster_name: str = typer.Option("", "--cluster-name", help="Local kind cluster name override."),
    kubeconfig_path: str = typer.Option("", "--kubeconfig-path", help="Local kubeconfig file path override."),
    project_root: str = typer.Option("", "--project-root", help="Writable project root; defaults to the bundled demo."),
) -> None:
    """Print pod/service/PVC status for the deployed namespace."""
    from olf.deployment.errors import DeploymentError

    context = _build_context(
        provider,
        profile="full",
        namespace=namespace,
        cluster_name=cluster_name,
        kubeconfig_path=kubeconfig_path,
        project_root=project_root,
    )
    engine = _build_engine(context, var_file="")
    try:
        report = engine.status()
    except DeploymentError as exc:
        raise typer.Exit(code=fail(str(exc))) from exc
    typer.echo(report.render())


def forward(
    provider: str = typer.Option("local", "--provider", help="Target deployment provider."),
    profile: str = typer.Option("full", "--profile", help="'full' or 'slim'."),
    namespace: str = typer.Option("", "--namespace", help="Kubernetes namespace override."),
    cluster_name: str = typer.Option("", "--cluster-name", help="Local kind cluster name override."),
    kubeconfig_path: str = typer.Option("", "--kubeconfig-path", help="Local kubeconfig file path override."),
    project_root: str = typer.Option("", "--project-root", help="Writable project root; defaults to the bundled demo."),
) -> None:
    """Start port-forwards for the deployed services (Ctrl-C to stop all)."""
    from olf.deployment.errors import DeploymentError

    context = _build_context(
        provider,
        profile=profile,
        namespace=namespace,
        cluster_name=cluster_name,
        kubeconfig_path=kubeconfig_path,
        project_root=project_root,
    )
    engine = _build_engine(context, var_file="")
    try:
        engine.forward()
    except DeploymentError as exc:
        raise typer.Exit(code=fail(str(exc))) from exc
