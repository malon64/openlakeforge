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
from olf.distribution import distribution_version_at
from olf.profile import StageName
from olf.project_activation import ProjectActivation, ProjectActivationError, commit_active
from olf.project_activation import active as active_activation
from olf.project_activation import publish as publish_activation
from olf.project_revision import ProjectRevisionError, materialize, verify
from olf.provider_contracts import ProviderContractError, parse_provider_contracts
from olf.tooling import docker as docker_tooling

_RELEASE = "openlakeforge-project"
_LOG_ARCHIVE = "openlakeforge-k8s-log-archive"
_PLATFORM_RELEASE = "dagster"
_ACTIVATION_LABEL = "openlakeforge.io/activation-revision"
# Contract-derived runtime settings that deliberately carry no OPENLAKEFORGE_
# prefix because the libraries reading them are not ours: libs.s3_artifacts
# resolves the object store through AWS_ENDPOINT_URL_S3, so a local or Azure
# stage that loses these reaches public S3 instead of its contracted endpoint.
# Terraform passed exactly this set to the code server. Credential *values*
# (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, OPENLINEAGE_API_KEY) stay out --
# those arrive by Secret reference.
_RUNTIME_ALIASES = (
    "AWS_ALLOW_HTTP",
    "AWS_DEFAULT_REGION",
    "AWS_ENDPOINT_URL_S3",
    "AWS_REGION",
    "AWS_S3_FORCE_PATH_STYLE",
    "OPENLINEAGE_ENDPOINT",
    "OPENLINEAGE_NAMESPACE",
    "OPENLINEAGE_URL",
)
_LOG_ARCHIVE_SCHEDULE = "*/15 * * * *"


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


def _log_archive_manifest(
    activation: ProjectActivation,
    *,
    namespace: str,
    env: list[dict[str, str]],
    secrets: list[str],
) -> dict[str, object]:
    """The compute-log archiver, rendered into the activation release.

    It runs `libs.k8s_log_archive`, which only exists inside the project-code
    image, so ADR 0002 puts it here rather than in a platform apply. Riding the
    activation release also keeps it on the digest the stage actually runs
    instead of whatever tag a platform apply last captured.
    """
    labels = {
        "app.kubernetes.io/name": _LOG_ARCHIVE,
        "openlakeforge.io/component": "observability",
    }
    return {
        "apiVersion": "batch/v1",
        "kind": "CronJob",
        "metadata": {"name": _LOG_ARCHIVE, "namespace": namespace, "labels": labels},
        "spec": {
            "schedule": _LOG_ARCHIVE_SCHEDULE,
            "concurrencyPolicy": "Forbid",
            "successfulJobsHistoryLimit": 1,
            "failedJobsHistoryLimit": 3,
            "jobTemplate": {
                "metadata": {"labels": labels},
                "spec": {
                    "backoffLimit": 1,
                    "template": {
                        "metadata": {"labels": labels},
                        "spec": {
                            "serviceAccountName": "dagster",
                            "automountServiceAccountToken": True,
                            "restartPolicy": "Never",
                            "containers": [
                                {
                                    "name": "archive-k8s-logs",
                                    "image": activation.project_code_image,
                                    "imagePullPolicy": "IfNotPresent",
                                    "command": ["python", "-m", "libs.k8s_log_archive"],
                                    "env": [
                                        *env,
                                        {"name": "OPENLAKEFORGE_LOG_ARCHIVE_SINCE_SECONDS", "value": "3600"},
                                    ],
                                    "envFrom": [{"secretRef": {"name": secret}} for secret in secrets],
                                }
                            ],
                        },
                    },
                },
            },
        },
    }


def _user_values(
    activation: ProjectActivation,
    *,
    contract_environ: Mapping[str, str],
    namespace: str,
    platform_globals: Mapping[str, Any],
) -> dict[str, object]:
    repository, digest = _image_parts(activation.project_code_image)
    env = [
        {"name": "OPENLAKEFORGE_PROJECT_REVISION", "value": activation.project_revision},
        {"name": "OPENLAKEFORGE_FLOE_MANIFEST_REVISION", "value": activation.floe_manifest_revision},
    ]
    for name in sorted(key for key in contract_environ if key.startswith("OPENLAKEFORGE_")):
        if "SECRET" not in name and "ACCESS_KEY" not in name:
            env.append({"name": name, "value": contract_environ[name]})
    for name in _RUNTIME_ALIASES:
        value = contract_environ.get(name)
        if value:
            env.append({"name": name, "value": value})
    # floe_dagster.kubernetes_runner submits its ephemeral Jobs into NAMESPACE
    # and falls back to "lakehouse", the pre-#133 single-stage namespace, when
    # it is unset -- every stage's Floe run would be RBAC-denied against a
    # namespace that no longer exists. Taken from the activation's own stage
    # rather than the environment, which is what makes it true per stage.
    env.append({"name": "NAMESPACE", "value": namespace})
    # The chart puts a list-form `env` straight onto the container, so a
    # secretKeyRef survives; a map would be flattened into a ConfigMap. The
    # ingestion-bot Secret stores its JWT under its own key name, and mounting
    # the whole Secret would expose only that name -- OpenLineage reads
    # OPENLINEAGE_API_KEY, so Terraform mapped the two explicitly and so must
    # this, or authenticated lineage silently stops working.
    jwt_secret = contract_environ.get("OPENLAKEFORGE_GOVERNANCE_INGESTION_BOT_SECRET_NAME", "")
    jwt_key = contract_environ.get("OPENLAKEFORGE_GOVERNANCE_INGESTION_BOT_JWT_KEY", "")
    if activation.capabilities.get("governance") and jwt_secret and jwt_key:
        env.append(
            {"name": "OPENLINEAGE_API_KEY", "valueFrom": {"secretKeyRef": {"name": jwt_secret, "key": jwt_key}}}
        )
    storage_secret = contract_environ.get("OPENLAKEFORGE_STORAGE_CREDENTIALS_SECRET_NAME", "")
    secrets = sorted(
        {
            storage_secret,
            contract_environ.get("OPENLAKEFORGE_CATALOG_FLOE_CREDENTIALS_SECRET_NAME", ""),
            jwt_secret,
        }
        - {""}
    )
    return {
        "global": dict(platform_globals),
        "extraManifests": [_log_archive_manifest(
                activation,
                namespace=namespace,
                env=env,
                # `libs.k8s_log_archive` reads the object store and nothing
                # else, and the Terraform CronJob it replaces mounted only
                # this Secret. Handing it catalog and governance credentials
                # would widen what a compromised log container reaches.
                secrets=[storage_secret] if storage_secret else [],
            )],
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
                    _ACTIVATION_LABEL: activation.activation_revision,
                },
                "deploymentAnnotations": {"openlakeforge.io/floe-manifest-revision": activation.floe_manifest_revision},
            }
        ],
    }


_USER_SUBCHART = "charts/dagster-user-deployments/"


def _user_chart(chart: Path, work_root: Path) -> Path:
    """Extract the pinned user-deployments subchart from the cached Dagster chart.

    The subchart travels unpacked inside the parent archive -- `prepare_chart`
    repackages a `helm pull --untar` tree to strip values schemas, so there is
    no nested `dagster-user-deployments-<version>.tgz` to lift out. Helm
    installs a chart directory as readily as an archive, so rebuild the
    subtree rather than re-tarring it.
    """
    destination = work_root / "dagster-user-deployments"
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(chart) as parent:
        for member in parent.getmembers():
            head, separator, relative = member.name.partition(_USER_SUBCHART)
            if not separator or "/" in head.rstrip("/") or not relative:
                continue
            target = (destination / relative).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise ActivationError(f"pinned Dagster chart {chart} contains an unsafe member {member.name!r}.")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                continue
            handle = parent.extractfile(member)
            if handle is None:
                raise ActivationError(f"could not read {member.name} from {chart}.")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(handle.read())
    if not (destination / "Chart.yaml").is_file():
        raise ActivationError(f"pinned Dagster chart {chart} does not contain dagster-user-deployments.")
    return destination


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


def _registry_host(repository: str) -> str:
    """The registry a repository reference points at, or "" for Docker Hub."""
    head = repository.split("/", 1)[0]
    return head if ("." in head or ":" in head or head == "localhost") else ""


def _ensure_image(provider: DeploymentProvider, image: str, *, env: Mapping[str, str]) -> None:
    platform = None
    # A revision can legitimately live outside the provider's registry -- GHCR
    # or Docker Hub, which is what the local workflow documents, or another
    # provider's registry after a move. Only the provider's own registry can be
    # authenticated here; for anything else the scoped Docker config holds no
    # credentials at all, so the pull has to use the caller's own.
    pull_env = docker_tooling.ambient_registry_env(env)
    if provider.context.provider is not Provider.LOCAL:
        facts = provider._foundation_facts  # type: ignore[attr-defined]
        repository = _image_parts(image)[0]
        if _registry_host(repository) == _registry_host(facts.project_code_repository):
            # Cloud command environments deliberately use a scoped Docker
            # config, so authenticate it rather than relying on ambient
            # credentials. Logging into a foreign host with an ECR password,
            # or as if it were an ACR, fails before the pull.
            provider.backend.registry_login(  # type: ignore[attr-defined]
                provider.tools, facts, repository=repository, env=env
            )
            pull_env = dict(env)
        platform = provider.config.images.image_platform  # type: ignore[attr-defined]
    provider.tools.docker.pull(image, platform=platform, env=pull_env)
    if provider.context.provider is Provider.LOCAL:
        from olf.deployment.local.images import load_image_into_kind

        load_image_into_kind(image, provider.config, provider.tools, env=env)  # type: ignore[arg-type,attr-defined]


def _platform_globals(provider: DeploymentProvider, *, kube_context: str, env: Mapping[str, str]) -> dict[str, Any]:
    """The `global` values the platform's Dagster release runs with.

    The activation release installs the user-deployments subchart standalone,
    so it inherits none of the parent's globals and falls back to the
    subchart's own defaults -- notably `postgresqlSecretName:
    dagster-postgresql-secret`, which is not what any stage's Terraform names
    its credentials. Reading the parent release keeps the two in agreement by
    construction instead of by a second copy of the naming rule.
    """
    result = provider.tools.helm.get_values(
        _PLATFORM_RELEASE, namespace=provider.context.namespace, kube_context=kube_context, env=env
    )
    if not result.ok:
        raise ActivationError(
            f"the {_PLATFORM_RELEASE!r} release is not installed in {provider.context.namespace}; "
            "run olf platform apply before activating a revision."
        )
    try:
        values = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ActivationError(f"could not read {_PLATFORM_RELEASE!r} release values: {exc}") from exc
    return dict((values or {}).get("global") or {})


def _kube_context(provider: DeploymentProvider) -> str:
    """The provider's resolved kube context.

    A cloud `DeploymentContext` carries an empty `kube_context` until the
    foundation outputs are read, and only the provider's command environment
    holds the resolved value. Reading the context directly leaves every
    kubectl-backed step of an activation without a cluster to talk to.
    """
    env = provider.env  # type: ignore[attr-defined]
    return env.get("KUBE_CONTEXT") or provider.context.kube_context


def release_runs_activation(
    provider: DeploymentProvider, activation_revision: str, *, env: Mapping[str, str]
) -> bool:
    """Whether the installed release is healthy *and* actually runs this activation.

    `helm status` alone reports a release that was manually rolled back, or
    restored from stale state, as perfectly healthy. Gating the idempotent
    skip on that would let reapplying the recorded revision decline to repair
    the very drift it should fix. The release renders the activation revision
    onto its deployments, so ask the release what it is running.
    """
    namespace = provider.context.namespace
    kube_context = _kube_context(provider)
    helm = provider.tools.helm
    if not helm.status(_RELEASE, namespace=namespace, kube_context=kube_context, env=env).ok:
        return False
    result = helm.get_values(_RELEASE, namespace=namespace, kube_context=kube_context, env=env)
    if not result.ok:
        return False
    try:
        values = json.loads(result.stdout or "{}") or {}
    except json.JSONDecodeError:
        return False
    return any(
        (deployment.get("deploymentLabels") or {}).get(_ACTIVATION_LABEL) == activation_revision
        for deployment in values.get("deployments") or []
    )


def deploy_revision(
    provider: DeploymentProvider, *, revision: str, store: RevisionStore, profile_name: str
) -> ProjectActivation:
    """Verify, render, roll out, then atomically make a revision active."""
    context = provider.context
    env = provider.env  # type: ignore[attr-defined]
    contract_dir = _contract_dir(context, env)
    kube_context = _kube_context(provider)
    platform_globals = _platform_globals(provider, kube_context=kube_context, env=env)
    raw_contract = contracts.load_provider_contracts(str(contract_dir), environ=env)
    if raw_contract is None:
        raise ActivationError(f"provider contracts are unavailable from {contract_dir}; run olf platform apply first.")
    try:
        binding = provider_binding_digest(raw_contract, topology=context.topology, stage=context.stage)
        manifest = verify(
            store,
            revision,
            running_distribution_version=distribution_version_at(context.paths.distribution_root),
        )
    except (ProjectRevisionError, ProviderContractError) as exc:
        raise ActivationError(str(exc)) from exc

    capabilities = {
        "analytics": context.features.analytics_enabled,
        "governance": context.features.governance_enabled,
    }
    previous = active_activation(store, stage=context.stage)
    if (
        previous is not None
        and previous.matches_inputs(
            ProjectActivation(
                deployment_profile=profile_name,
                provider=context.provider.value,
                stage=context.stage,
                project_name=manifest.project_name,
                project_revision=manifest.revision,
                distribution_version=manifest.distribution_version,
                project_code_image=manifest.project_code_image,
                floe_manifest_revision=previous.floe_manifest_revision,
                provider_binding_digest=binding,
                capabilities=capabilities,
            )
        )
        and release_runs_activation(provider, previous.activation_revision, env=env)
    ):
        # Reapplying the active revision must not touch the cluster or the ops
        # bucket at all. Deciding this only after Floe has been regenerated
        # would already have performed the work idempotency exists to skip.
        return previous
    # Only once a rollout is actually required: pulling here, and on local
    # reloading into kind, is exactly the cluster mutation the no-op path
    # promises not to perform, and it would make an idempotent redeploy fail
    # on a transient registry outage.
    _ensure_image(provider, manifest.project_code_image, env=env)
    with tempfile.TemporaryDirectory(prefix="project-activation.", dir=context.paths.work_root) as temporary:
        root = materialize(store, manifest, Path(temporary) / "project")
        with contract_env.applied_contract_environment(
            contract_terraform_dir=contract_dir,
            repo_root=root,
            namespace=context.namespace,
            kube_context=kube_context,
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
                capabilities=capabilities,
            ).resolved()
            if previous == activation and release_runs_activation(provider, activation.activation_revision, env=env):
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
                yaml.safe_dump(
                    _user_values(
                        activation,
                        contract_environ=contract_environ,
                        namespace=context.namespace,
                        platform_globals=platform_globals,
                    ),
                    sort_keys=False,
                )
            )
            publish_activation(store, activation)
            provider.tools.helm.upgrade_install(
                _RELEASE, chart, namespace=context.namespace, values=values, kube_context=kube_context, env=env
            )
            try:
                commit_active(store, activation)
            except ProjectActivationError:
                if previous is None:
                    provider.tools.helm.uninstall(
                        _RELEASE, namespace=context.namespace, kube_context=kube_context, env=env
                    )
                else:
                    rollback_values = Path(temporary) / "rollback-values.yaml"
                    rollback_values.write_text(
                        yaml.safe_dump(
                            _user_values(
                                previous,
                                contract_environ=contract_environ,
                                namespace=context.namespace,
                                platform_globals=platform_globals,
                            ),
                            sort_keys=False,
                        )
                    )
                    provider.tools.helm.upgrade_install(
                        _RELEASE,
                        chart,
                        namespace=context.namespace,
                        values=rollback_values,
                        kube_context=kube_context,
                        env=env,
                    )
                raise
    return activation
