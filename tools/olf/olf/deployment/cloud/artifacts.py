"""Dynamic cloud (AWS/Azure) artifact deployment.

Port of `scripts/{aws,azure}/stack/deploy-artifacts.sh`. Preserves the exact
ordering: reconcile catalog namespaces -> generate Floe runtime artifacts ->
activate/publish the immutable Floe revision -> build/push the project-code
image using that revision -> upload legacy Floe manifests -> point Dagster
at the image -> deploy optional Superset/OpenMetadata artifacts.

Unlike local (which only builds the project-code image when the tag is
still the `local` placeholder), cloud always builds and pushes a freshly
tagged image - there is no "reuse an already-pushed image" shortcut in the
shell scripts this replaces.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from olf import k8s, log
from olf.deployment import contract_env
from olf.deployment.artifact_steps import (
    activate_runtime_revision,
    deploy_optional_layer_artifacts,
    sync_catalog_namespaces,
    upload_runtime_manifests,
)
from olf.deployment.cloud.backend import CloudBackend, FoundationFacts
from olf.deployment.cloud.config import CloudDeploymentConfig
from olf.deployment.cloud.images import build_and_push_project_code_image, resolve_effective_images
from olf.deployment.engine import Toolkit


def artifacts_deploy(
    config: CloudDeploymentConfig,
    tools: Toolkit,
    backend: CloudBackend,
    facts: FoundationFacts,
    *,
    env: Mapping[str, str],
) -> None:
    with contract_env.applied_contract_environment(
        contract_terraform_dir=config.paths.platform_terraform_dir,
        repo_root=config.paths.repo_root,
        namespace=config.namespace,
        kube_context=facts.kube_context,
        kubeconfig_path=config.paths.kubeconfig_path,
        port_forward_log_prefix=config.paths.port_forward_log_prefix,
    ) as contract_environ:
        log.step(f"Reconciling catalog namespaces from the domain descriptors for {backend.scope}...")
        sync_catalog_namespaces()

        log.step(f"Generating {backend.scope} product Floe manifests for namespace '{config.namespace}'...")
        backend.generate_floe_manifests(
            config,
            tools,
            repo_root=config.paths.repo_root,
            namespace=config.namespace,
            governance_enabled=config.features.governance_enabled,
            environ=contract_environ,
            env=env,
        )

        transport = backend.artifact_transport()
        log.step("Publishing and verifying immutable Floe runtime-artifact revision...")
        revision_id = activate_runtime_revision(config.floe.runtime_artifact_dir, via=transport)
        os.environ["FLOE_MANIFEST_REVISION"] = revision_id

        build_and_push_project_code_image(config, tools, backend, facts, env=env, revision=revision_id)

        log.step(f"Publishing product Floe runtime artifacts to the {backend.scope} ops bucket...")
        upload_runtime_manifests(config.floe.runtime_artifact_dir, via=transport)

        project_code_image = resolve_effective_images(config.images, facts).project_code_image
        log.step(f"Pointing Dagster at project-code image {project_code_image}...")
        k8s.set_project_code_image(project_code_image, config.namespace)

        os.environ.setdefault("OPENMETADATA_ALLOW_MISSING_ASSETS", "true")
        deploy_optional_layer_artifacts(os.environ)

        log.step(f"Dynamic OpenLakeForge {backend.scope} artifacts are deployed.")
