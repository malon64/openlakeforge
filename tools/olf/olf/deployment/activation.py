"""Stage activation of a verified ProjectRevision (#115)."""

from __future__ import annotations

import hashlib
import json
import tarfile
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from olf import contracts
from olf.artifact_store import RevisionStore
from olf.deployment import contract_env
from olf.deployment.artifact_steps import (
    activate_runtime_revision,
    deploy_optional_layer_artifacts,
    sync_catalog_namespaces,
)
from olf.deployment.charts import prepare_chart
from olf.deployment.context import DeploymentContext, Provider
from olf.deployment.engine import DeploymentProvider
from olf.deployment.errors import DeploymentPreconditionError
from olf.deployment.floe_manifests import generate_aws_manifests, generate_local_manifests
from olf.profile import StageName
from olf.project_activation import ProjectActivation, ProjectActivationError, commit_active
from olf.project_activation import active as active_activation
from olf.project_activation import publish as publish_activation
from olf.project_revision import ProjectRevisionError, materialize, verify
from olf.provider_contracts import ProviderContractError, parse_provider_contracts

_RELEASE = "openlakeforge-project"


class ActivationError(DeploymentPreconditionError):
    """A revision cannot be activated while preserving the current release."""


def provider_binding_digest(raw_contract: Mapping[str, Any], *, topology, stage: StageName) -> str:  # noqa: ANN001
    """Hash the selected non-secret provider contract binding deterministically."""
    parsed = parse_provider_contracts(raw_contract, topology)
    if parsed.compatibility_v2 or parsed.schema_version != "3.0.0":
        raise ActivationError(
            "olf project deploy requires a native provider-contract v3 platform; v2 is DEV compatibility only."
        )
    selected = parsed.for_stage(stage)
    payload = {
        "deployment": dict(parsed.deployment),
        "shared": {
            "ops_storage": dict(selected.shared.values["ops_storage"]),
            "identity": dict(selected.shared.values["identity"]),
        },
        "stage": {
            "name": selected.name.value,
            "namespace": selected.namespace,
            "storage": dict(selected.storage),
            "catalog": dict(selected.catalog),
            "query": dict(selected.query),
            "orchestration": dict(selected.orchestration),
            "activation": dict(selected.activation),
            "endpoints": dict(selected.endpoints),
            "runtime_identity": dict(selected.runtime_identity),
            "reporting": None if selected.reporting is None else dict(selected.reporting),
            "governance": None if selected.governance is None else dict(selected.governance),
        },
    }
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=list).encode()
    return "sha256:" + hashlib.sha256(rendered).hexdigest()


def _contract_dir(context: DeploymentContext, environ: Mapping[str, str]) -> Path:
    return Path(environ.get("OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR", context.paths.platform_terraform_dir)).resolve()


def _image_parts(image: str) -> tuple[str, str]:
    repository, digest = image.split("@", 1)
    return repository, digest


def _user_values(activation: ProjectActivation, *, contract_environ: Mapping[str, str]) -> dict[str, object]:
    repository, digest = _image_parts(activation.project_code_image)
    env = [
        {"name": "OPENLAKEFORGE_PROJECT_REVISION", "value": activation.project_revision},
        {"name": "OPENLAKEFORGE_FLOE_MANIFEST_REVISION", "value": activation.floe_manifest_revision},
    ]
    for name in sorted(key for key in contract_environ if key.startswith("OPENLAKEFORGE_")):
        if "SECRET" not in name and "ACCESS_KEY" not in name:
            env.append({"name": name, "value": contract_environ[name]})
    secrets = sorted(
        {
            contract_environ.get("OPENLAKEFORGE_STORAGE_CREDENTIALS_SECRET_NAME", ""),
            contract_environ.get("OPENLAKEFORGE_CATALOG_FLOE_CREDENTIALS_SECRET_NAME", ""),
            contract_environ.get("OPENLAKEFORGE_GOVERNANCE_INGESTION_BOT_SECRET_NAME", ""),
        }
        - {""}
    )
    return {
        "serviceAccount": {"create": False, "name": "dagster"},
        "deployments": [
            {
                "name": "openlakeforge-dagster",
                "image": {"repository": repository, "digest": digest, "pullPolicy": "IfNotPresent"},
                "dagsterApiGrpcArgs": ["--module-name", "lakehouse_code.definitions"],
                "port": 3030,
                "includeConfigInLaunchedRuns": {"enabled": True},
                "env": env,
                "envSecrets": [{"name": secret} for secret in secrets],
                "deploymentLabels": {
                    "openlakeforge.io/project-revision": activation.project_revision,
                    "openlakeforge.io/activation-revision": activation.activation_revision,
                },
                "deploymentAnnotations": {"openlakeforge.io/floe-manifest-revision": activation.floe_manifest_revision},
            }
        ],
    }


def _user_chart(chart: Path, work_root: Path) -> Path:
    """Extract the pinned user-deployments subchart from the cached Dagster chart."""
    with tarfile.open(chart) as parent:
        member = next((item for item in parent.getmembers() if "/charts/dagster-user-deployments-" in item.name), None)
        if member is None:
            raise ActivationError(f"pinned Dagster chart {chart} does not contain dagster-user-deployments.")
        handle = parent.extractfile(member)
        if handle is None:
            raise ActivationError(f"could not read user-deployments chart from {chart}.")
        extracted = work_root / "dagster-user-deployments.tgz"
        extracted.write_bytes(handle.read())
    return extracted


def _generate_floe(
    provider: DeploymentProvider, *, repo_root: Path, contract_environ: Mapping[str, str], env: Mapping[str, str]
) -> str:
    config = provider.config  # type: ignore[attr-defined]
    context = provider.context
    if context.provider is Provider.AWS:
        generate_aws_manifests(config.floe, provider.tools, repo_root=repo_root, environ=contract_environ, env=env)
        transport = "direct"
    else:
        generate_local_manifests(
            config.floe,
            provider.tools,
            repo_root=repo_root,
            distribution_root=context.paths.distribution_root,
            namespace=context.namespace,
            governance_enabled=context.features.governance_enabled,
            environ=contract_environ,
            env=env,
        )
        transport = "port-forward"
    return activate_runtime_revision(config.floe.runtime_artifact_dir, via=transport)


def _ensure_image(provider: DeploymentProvider, image: str, *, env: Mapping[str, str]) -> None:
    platform = None
    if provider.context.provider is not Provider.LOCAL:
        # Cloud command environments deliberately use a scoped Docker config.
        # Authenticate that config immediately before the digest probe rather
        # than relying on a developer's ambient Docker credentials.
        provider.backend.registry_login(  # type: ignore[attr-defined]
            provider.tools,
            provider._foundation_facts,  # type: ignore[attr-defined]
            repository=_image_parts(image)[0],
            env=env,
        )
        platform = provider.config.images.image_platform  # type: ignore[attr-defined]
    provider.tools.docker.pull(image, platform=platform, env=env)
    if provider.context.provider is Provider.LOCAL:
        from olf.deployment.local.images import load_image_into_kind

        load_image_into_kind(image, provider.config, provider.tools, env=env)  # type: ignore[arg-type,attr-defined]


def deploy_revision(
    provider: DeploymentProvider, *, revision: str, store: RevisionStore, profile_name: str
) -> ProjectActivation:
    """Verify, render, roll out, then atomically make a revision active."""
    context = provider.context
    env = provider.env  # type: ignore[attr-defined]
    contract_dir = _contract_dir(context, env)
    raw_contract = contracts.load_provider_contracts(str(contract_dir), environ=env)
    if raw_contract is None:
        raise ActivationError(f"provider contracts are unavailable from {contract_dir}; run olf platform apply first.")
    try:
        binding = provider_binding_digest(raw_contract, topology=context.topology, stage=context.stage)
        manifest = verify(
            store,
            revision,
            running_distribution_version=context.paths.distribution_root.joinpath(
                "release/component-catalog.yaml"
            ).exists()
            and __import__("olf.distribution", fromlist=["runtime_layout"]).runtime_layout().distribution_version
            or None,
        )
    except (ProjectRevisionError, ProviderContractError) as exc:
        raise ActivationError(str(exc)) from exc
    _ensure_image(provider, manifest.project_code_image, env=env)

    previous = active_activation(store, stage=context.stage)
    with tempfile.TemporaryDirectory(prefix="project-activation.", dir=context.paths.work_root) as temporary:
        root = materialize(store, manifest, Path(temporary) / "project")
        with contract_env.applied_contract_environment(
            contract_terraform_dir=contract_dir,
            repo_root=root,
            namespace=context.namespace,
            kube_context=context.kube_context,
            kubeconfig_path=context.paths.kubeconfig_path,
            port_forward_log_prefix=context.paths.port_forward_log_prefix,
            environ=env,
            topology=context.topology,
            stage=context.stage,
        ) as contract_environ:
            sync_catalog_namespaces()
            floe_revision = _generate_floe(provider, repo_root=root, contract_environ=contract_environ, env=env)
            activation = ProjectActivation(
                deployment_profile=profile_name,
                provider=context.provider.value,
                stage=context.stage,
                project_name=manifest.project_name,
                project_revision=manifest.revision,
                distribution_version=manifest.distribution_version,
                project_code_image=manifest.project_code_image,
                floe_manifest_revision=floe_revision,
                provider_binding_digest=binding,
                capabilities={
                    "analytics": context.features.analytics_enabled,
                    "governance": context.features.governance_enabled,
                },
            ).resolved()
            if (
                previous == activation
                and provider.tools.helm.status(
                    _RELEASE, namespace=context.namespace, kube_context=context.kube_context, env=env
                ).ok
            ):
                return activation
            if activation.capabilities["analytics"] or activation.capabilities["governance"]:
                deploy_optional_layer_artifacts(contract_environ)
            config = provider.config  # type: ignore[attr-defined]
            prepare_chart(
                config.charts["dagster"],
                helm=provider.tools.helm,
                paths=config.paths,
                env=env,
                retry_policy=config.terraform.apply_retry,
            )
            chart = _user_chart(config.charts["dagster"].package_path, Path(temporary))
            values = Path(temporary) / "values.yaml"
            values.write_text(
                yaml.safe_dump(_user_values(activation, contract_environ=contract_environ), sort_keys=False)
            )
            publish_activation(store, activation)
            provider.tools.helm.upgrade_install(
                _RELEASE, chart, namespace=context.namespace, values=values, kube_context=context.kube_context, env=env
            )
            try:
                commit_active(store, activation)
            except ProjectActivationError:
                if previous is None:
                    provider.tools.helm.uninstall(
                        _RELEASE, namespace=context.namespace, kube_context=context.kube_context, env=env
                    )
                else:
                    rollback_values = Path(temporary) / "rollback-values.yaml"
                    rollback_values.write_text(
                        yaml.safe_dump(_user_values(previous, contract_environ=contract_environ), sort_keys=False)
                    )
                    provider.tools.helm.upgrade_install(
                        _RELEASE,
                        chart,
                        namespace=context.namespace,
                        values=rollback_values,
                        kube_context=context.kube_context,
                        env=env,
                    )
                raise
    return activation
