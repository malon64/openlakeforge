"""Dynamic local artifact deployment.

Port of `scripts/local/stack/deploy-artifacts.sh`. Calls the existing Python
library functions directly (in-process) instead of shelling out to
`uv run olf ...` or sourcing `scripts/contracts/load-runtime-env.sh`.
Preserves the exact ordering: load provider contracts -> reconcile catalog
namespaces -> generate Floe runtime artifacts -> activate/publish the
immutable Floe revision -> build/load the project-code image using that
revision -> point Dagster at the image -> deploy optional Superset/
OpenMetadata artifacts.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

import typer

from olf import config as olf_config
from olf import contracts, k8s, layers, log, revision, s3
from olf.deployment.engine import Toolkit
from olf.deployment.errors import DeploymentPreconditionError
from olf.deployment.local import floe_manifests, images
from olf.deployment.local.config import LocalDeploymentConfig


class ArtifactOperationError(DeploymentPreconditionError):
    """Raised when a called `olf` artifact operation fails."""


def _run_cli(description: str, fn, *args, **kwargs) -> None:  # noqa: ANN001, ANN002, ANN003
    try:
        fn(*args, **kwargs)
    except typer.Exit as exc:
        raise ArtifactOperationError(f"{description} failed (see the ERROR above).") from exc


@contextmanager
def applied_contract_environment(config: LocalDeploymentConfig) -> Iterator[dict[str, str]]:
    """Apply the resolved provider-contract environment onto `os.environ`.

    Replaces sourcing `scripts/contracts/load-runtime-env.sh`: calls
    `olf.contracts.load_provider_contracts`/`build_contract_env` directly and
    applies the exports/unsets to `os.environ` for the duration of the block,
    restoring the previous values on exit. `DeploymentContext.command_env`
    itself never mutates `os.environ` - this is the documented, deliberate
    bridge to the existing `os.environ`-reading library modules (`olf.k8s`,
    `olf.config`, `olf.s3`, `olf.layers`).
    """
    provider_contracts = contracts.load_provider_contracts(str(config.paths.platform_terraform_dir))
    exports, unsets = contracts.build_contract_env(os.environ, provider_contracts, repo_root=config.paths.repo_root)

    extra = {
        "OPENLAKEFORGE_REPO_ROOT": str(config.paths.repo_root),
        "NAMESPACE": config.namespace,
        "KUBE_CONTEXT": config.kube_context,
        "KUBECONFIG": str(config.paths.kubeconfig_path),
        "OPENLAKEFORGE_PORT_FORWARD_LOG_PREFIX": str(config.paths.port_forward_log_prefix),
    }

    previous: dict[str, str | None] = {}
    applied = {**exports, **extra}
    for name, value in applied.items():
        previous[name] = os.environ.get(name)
        os.environ[name] = value
    for name in unsets:
        previous.setdefault(name, os.environ.get(name))
        os.environ.pop(name, None)

    try:
        yield dict(os.environ)
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def sync_catalog_namespaces() -> None:
    from olf.commands.catalog import catalog_sync_namespaces

    _run_cli("Catalog namespace reconciliation", catalog_sync_namespaces, dry_run=False, prune=None)


def _artifact_bucket() -> str:
    bucket = olf_config.env("OPENLAKEFORGE_OPS_BUCKET_NAME") or olf_config.env("OPENLAKEFORGE_ARTIFACT_BUCKET_NAME")
    if not bucket:
        raise ArtifactOperationError("no ops/artifact bucket resolved from the contract environment.")
    return bucket


@contextmanager
def _artifact_storage_client_via_port_forward(bucket: str) -> Iterator[object]:
    namespace = olf_config.namespace()
    secret_name = olf_config.env("OPENLAKEFORGE_STORAGE_CREDENTIALS_SECRET_NAME")
    service = olf_config.env("OPENLAKEFORGE_STORAGE_S3_SERVICE_NAME", "seaweedfs-s3")
    remote_port = int(olf_config.env("OPENLAKEFORGE_STORAGE_S3_SERVICE_PORT", "8333"))
    with s3.port_forward_client(
        bucket,
        service=service,
        remote_port=remote_port,
        namespace=namespace,
        access_key_id=k8s.secret_value(
            secret_name, olf_config.env("OPENLAKEFORGE_STORAGE_ACCESS_KEY_ID_KEY", "AWS_ACCESS_KEY_ID"), namespace
        ),
        secret_access_key=k8s.secret_value(
            secret_name,
            olf_config.env("OPENLAKEFORGE_STORAGE_SECRET_ACCESS_KEY_KEY", "AWS_SECRET_ACCESS_KEY"),
            namespace,
        ),
        region=olf_config.env("OPENLAKEFORGE_STORAGE_REGION", "us-east-1"),
    ) as client:
        yield client


def activate_runtime_revision(runtime_root: Path) -> str:
    uploads = s3.discover_runtime_artifacts(runtime_root)
    if not uploads:
        raise ArtifactOperationError(f"no rendered Floe runtime artifacts found under {runtime_root}.")
    bucket = _artifact_bucket()
    try:
        with _artifact_storage_client_via_port_forward(bucket) as client:
            manifest = revision.activate(client, bucket, uploads)
    except revision.RevisionError as exc:
        raise ArtifactOperationError(str(exc)) from exc
    return manifest.revision


def upload_runtime_manifests(runtime_root: Path) -> None:
    from olf.commands.artifacts import artifacts_upload_manifests

    _run_cli(
        "Legacy Floe manifest upload",
        artifacts_upload_manifests,
        via="port-forward",
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


def artifacts_deploy(config: LocalDeploymentConfig, tools: Toolkit, *, env: Mapping[str, str]) -> None:
    with applied_contract_environment(config) as contract_env:
        log.step("Reconciling Polaris namespaces from the domain descriptors...")
        sync_catalog_namespaces()

        log.step(f"Generating local product Floe manifests for namespace '{config.namespace}'...")
        floe_manifests.generate_local_manifests(
            config.floe,
            tools,
            repo_root=config.paths.repo_root,
            namespace=config.namespace,
            governance_enabled=config.features.governance_enabled,
            environ=contract_env,
            env=env,
        )

        log.step("Publishing and verifying immutable Floe runtime-artifact revision...")
        revision_id = activate_runtime_revision(config.floe.runtime_artifact_dir)
        os.environ["FLOE_MANIFEST_REVISION"] = revision_id

        if config.images.project_code_tag == "local":
            images.prepare_project_code_image(config, tools, env=env, revision=revision_id)
        else:
            log.step("Publishing legacy Floe manifests for the supplied project-code image...")
            upload_runtime_manifests(config.floe.runtime_artifact_dir)

        log.step(f"Pointing Dagster at project-code image {config.images.project_code_image}...")
        k8s.set_project_code_image(config.images.project_code_image, config.namespace)

        os.environ.setdefault("OPENMETADATA_ALLOW_MISSING_ASSETS", "true")
        deploy_optional_layer_artifacts(os.environ)

        log.step("Dynamic OpenLakeForge local artifacts are deployed.")
