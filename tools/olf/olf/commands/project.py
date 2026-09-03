"""Commands for validating, building, and verifying writable OpenLakeForge data projects."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path

import typer

from olf.commands._shared import deployment_context_for_profile, fail
from olf.project import ProjectSpec, validate_project

app = typer.Typer(help="Validate and build writable OpenLakeForge data projects.")

revision_app = typer.Typer(help="Inspect and verify a published ProjectRevision (#154).")
app.add_typer(revision_app, name="revision")


def _profile_provider(context, *, var_file: str = ""):  # noqa: ANN001, ANN202
    import os

    from olf.deployment.engine import Toolkit, build_provider
    from olf.deployment.errors import DeploymentError

    try:
        env = context.command_env(base=os.environ)
        return build_provider(
            context, toolkit=Toolkit.default(environ=env), environ=env, var_file=Path(var_file) if var_file else None
        )
    except DeploymentError as exc:
        raise typer.Exit(code=fail(str(exc))) from exc


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
    output: str = typer.Option("", "--output", help="Publish into this local directory instead of the ops bucket."),
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
        with _build_store_for_project(layout.project_root, via=via, output=output) as store:
            publish(store, manifest, spec)
    except (ProjectRevisionError, ArtifactStoreError) as exc:
        raise typer.Exit(code=fail(str(exc))) from exc
    typer.echo(manifest.to_json() if json_output else manifest.revision)


@app.command("image")
def image(
    profile_file: str = typer.Option(..., "--file", "-f", help="Deployment Profile v1 path."),
    var_file: str = typer.Option("", "--var-file", help="Provider-specific Terraform tfvars override."),
) -> None:
    """Build the project-code image and print the reference `olf project build --image` expects.

    The profile's own directory is the project root, matching `olf project
    deploy`. Cloud providers push to the foundation's registry and print a
    digest-pinned reference; local builds load into kind and print a tag,
    which is not publishable as a revision.
    """
    from olf.deployment.context import Provider
    from olf.deployment.errors import DeploymentError
    from olf.project_revision import ProjectRevisionError, resolve_image_digest

    context = deployment_context_for_profile(profile_file, var_file=var_file)
    provider = _profile_provider(context, var_file=var_file)
    try:
        reference = provider.build_project_image()
        if context.provider is not Provider.LOCAL:
            reference = resolve_image_digest(reference)
    except (DeploymentError, ProjectRevisionError) as exc:
        raise typer.Exit(code=fail(str(exc))) from exc
    typer.echo(reference)


@app.command("deploy")
def deploy(
    profile_file: str = typer.Option(..., "--file", "-f", help="Deployment Profile v1 path."),
    stage: str = typer.Option(..., "--stage", help="Enabled target stage: dev, uat, or prod."),
    revision: str = typer.Option(..., "--revision", help="Published ProjectRevision, e.g. sha256:<digest>."),
    var_file: str = typer.Option("", "--var-file", help="Provider-specific Terraform tfvars override."),
) -> None:
    """Activate an existing immutable revision without building source or Terraform."""
    from olf.artifact_store import ArtifactStoreError
    from olf.deployment.activation import ActivationError, deploy_revision
    from olf.deployment.errors import DeploymentError
    from olf.profile import load_deployment_profile
    from olf.project_activation import ProjectActivationError
    from olf.project_revision import ProjectRevisionError

    context = deployment_context_for_profile(profile_file, stage=stage, var_file=var_file)
    provider = _profile_provider(context, var_file=var_file)
    via = "direct" if context.provider.value == "aws" else "port-forward"
    try:
        profile = load_deployment_profile(Path(profile_file))
        with _build_store_for_project(Path(profile_file).resolve().parent, via=via, output="") as store:
            activation = deploy_revision(
                provider,
                revision=revision,
                store=store,
                profile_name=profile.name,
            )
    except (ActivationError, ArtifactStoreError, DeploymentError, ProjectActivationError, ProjectRevisionError) as exc:
        raise typer.Exit(code=fail(str(exc))) from exc
    typer.echo(activation.activation_revision)


@app.command("status")
def status(
    profile_file: str = typer.Option(..., "--file", "-f", help="Deployment Profile v1 path."),
    stage: str = typer.Option("", "--stage", help="One enabled stage; defaults to all enabled stages."),
    json_output: bool = typer.Option(False, "--json", help="Render stable JSON."),
) -> None:
    """Compare each stage's immutable active pointer with its user deployment."""
    from olf.artifact_store import ArtifactStoreError, S3RevisionStore, artifact_bucket, artifact_storage_client
    from olf.deployment.contract_env import applied_contract_environment
    from olf.profile import StageName
    from olf.project_activation import ProjectActivationError
    from olf.project_activation import active as active_activation

    selected = (StageName(stage),) if stage else None
    initial = deployment_context_for_profile(profile_file, stage=stage or "dev")
    stages = selected or initial.enabled_stages
    reports: list[dict[str, object]] = []
    via = "direct" if initial.provider.value == "aws" else "port-forward"
    try:
        for item in stages:
            context = deployment_context_for_profile(profile_file, stage=item.value)
            provider = _profile_provider(context)
            contract_dir = Path(
                provider.env.get("OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR", context.paths.platform_terraform_dir)
            ).resolve()
            kube_context = provider.env.get("KUBE_CONTEXT") or context.kube_context
            with applied_contract_environment(
                contract_terraform_dir=contract_dir,
                repo_root=context.paths.repo_root,
                namespace=context.namespace,
                kube_context=kube_context,
                kubeconfig_path=context.paths.kubeconfig_path,
                port_forward_log_prefix=context.paths.port_forward_log_prefix,
                environ=provider.env,
                topology=context.topology,
                stage=context.stage,
            ):
                with artifact_storage_client(via, artifact_bucket()) as client:
                    store = S3RevisionStore(client, artifact_bucket())
                    activation = active_activation(store, stage=item)
                    if activation is None:
                        reports.append({"stage": item.value, "state": "inactive", "recorded": None, "observed": None})
                        continue
                    observed_ok = provider.tools.helm.status(
                        "openlakeforge-project",
                        namespace=context.namespace,
                        kube_context=kube_context,
                        env=provider.env,
                    ).ok
                    reports.append(
                        {
                            "stage": item.value,
                            "state": "active" if observed_ok else "drifted",
                            "recorded": {
                                "activation_revision": activation.activation_revision,
                                "project_revision": activation.project_revision,
                                "floe_manifest_revision": activation.floe_manifest_revision,
                            },
                            "observed": {"release": "openlakeforge-project", "present": observed_ok},
                        }
                    )
    except (ArtifactStoreError, ProjectActivationError) as exc:
        raise typer.Exit(code=fail(str(exc))) from exc
    if json_output:
        typer.echo(json.dumps({"stages": reports}, sort_keys=True))
        return
    for report in reports:
        recorded = report["recorded"]
        suffix = "" if recorded is None else f" ({recorded['project_revision']})"  # type: ignore[index]
        typer.echo(f"{report['stage']}: {report['state']}{suffix}")


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


@contextmanager
def _build_store_for_project(project_root: Path, *, via: str, output: str) -> Iterator[object]:
    """Open the build store under the profile contract when one is available."""
    if output or not (project_root / "openlakeforge.yaml").is_file():
        with _revision_store(via=via, output=output) as store:
            yield store
        return
    from olf.artifact_store import ArtifactStoreError
    from olf.deployment.contract_env import applied_contract_environment
    from olf.deployment.errors import DeploymentError
    from olf.k8s import KubectlError

    context = deployment_context_for_profile(str(project_root / "openlakeforge.yaml"))
    provider = _profile_provider(context)
    contract_dir = Path(
        provider.env.get("OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR", context.paths.platform_terraform_dir)
    ).resolve()
    with ExitStack() as stack:
        try:
            # Building against an unreachable platform is the same outcome as
            # building with no bucket resolved, and `olf project build` already
            # fails closed on that. Reaching the caller as a raw kubectl or
            # Terraform error would instead surface a traceback. Only opening
            # the store is wrapped: a failure in the body belongs to whatever
            # the caller is doing with it, and reporting that as an artifact
            # store problem would misname it.
            stack.enter_context(
                applied_contract_environment(
                    contract_terraform_dir=contract_dir,
                    repo_root=project_root,
                    namespace=context.namespace,
                    kube_context=provider.env.get("KUBE_CONTEXT") or context.kube_context,
                    kubeconfig_path=context.paths.kubeconfig_path,
                    port_forward_log_prefix=context.paths.port_forward_log_prefix,
                    environ=provider.env,
                    topology=context.topology,
                    stage=context.stage,
                )
            )
            store = stack.enter_context(_revision_store(via=via, output=output))
        except (DeploymentError, KubectlError) as exc:
            raise ArtifactStoreError(
                f"could not resolve the artifact store from the {context.provider.value} platform contract: {exc}"
            ) from exc
        yield store
