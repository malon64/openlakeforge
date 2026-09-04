from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from olf import project_activation, project_revision
from olf.artifact_store import FilesystemRevisionStore
from olf.deployment import activation as activation_module
from olf.deployment.context import DeploymentContext
from olf.distribution import distribution_version_at
from olf.profile import StageName, resolve_topology, validate_deployment_profile
from olf.project import ProjectSpec

FIXTURES = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).resolve().parents[3]
_IMAGE = "ghcr.io/openlakeforge/project-code@sha256:" + "a" * 64
_FLOE = "sha256:" + "c" * 64


def _contract() -> dict:
    return json.loads((FIXTURES / "local-provider-contracts-v3.json").read_text())


def _topology(contract: dict):
    """Both stages enabled, with each stage's capabilities read from the contract itself."""
    stages = {
        name: {
            "enabled": True,
            "capabilities": {"analytics": "reporting" in stage, "governance": "governance" in stage},
        }
        for name, stage in contract["stages"].items()
    }
    return resolve_topology(
        validate_deployment_profile(
            {
                "apiVersion": "openlakeforge.io/v1alpha1",
                "kind": "DeploymentProfile",
                "metadata": {"name": contract["deployment"]["profile_name"]},
                "spec": {"provider": {"type": "local"}, "preset": "full", "stages": stages},
            }
        )
    )


class _Helm:
    """Records rollouts and reports readiness per namespace, as Helm does per release."""

    def __init__(self) -> None:
        self.rollouts: list[tuple[str, dict]] = []
        self.uninstalled: list[str] = []
        self.ready: set[str] = set()
        self.installed: dict[str, dict] = {}
        self.secret_suffix = "creds"
        self.fails = False

    def status(self, release: str, *, namespace: str, kube_context=None, env=None):  # noqa: ANN001, ANN202, ARG002
        return SimpleNamespace(ok=namespace in self.ready)

    def platform_globals(self, namespace: str) -> dict[str, str]:
        return {"postgresqlSecretName": f"postgresql-dagster-{namespace}-{self.secret_suffix}"}

    def get_values(self, release, *, namespace, kube_context=None, env=None):  # noqa: ANN001, ANN202, ARG002
        if release == "dagster":
            # The platform release the activation inherits its globals from.
            return SimpleNamespace(
                ok=True,
                stdout=json.dumps({"global": self.platform_globals(namespace)}),
            )
        installed = self.installed.get(namespace)
        return SimpleNamespace(ok=installed is not None, stdout=json.dumps(installed or {}))

    def upgrade_install(self, release, chart, *, namespace, values, kube_context=None, env=None):  # noqa: ANN001, ANN202, ARG002
        if self.fails:
            raise RuntimeError("helm upgrade --atomic rolled back")
        rendered = yaml.safe_load(values.read_text())
        self.rollouts.append((namespace, rendered))
        self.installed[namespace] = rendered
        self.ready.add(namespace)

    def uninstall(self, release, *, namespace, kube_context=None, env=None) -> None:  # noqa: ANN001, ARG002
        self.uninstalled.append(namespace)
        self.installed.pop(namespace, None)
        self.ready.discard(namespace)


class _Provider:
    def __init__(self, context: DeploymentContext, helm: _Helm) -> None:
        self.context = context
        self.env: dict[str, str] = {}
        self.tools = SimpleNamespace(helm=helm)
        self.config = SimpleNamespace(
            charts={"dagster": SimpleNamespace(package_path=Path("unused.tgz"))},
            paths=context.paths,
            terraform=SimpleNamespace(apply_retry=None),
            floe=SimpleNamespace(image="ghcr.io/malon64/floe:0.6.11", version="0.6.11", runtime="image"),
        )


@pytest.fixture
def harness(external_project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    """A published revision plus an activation path with its I/O collaborators stubbed.

    Floe rendering, image pulls, chart preparation and the contract environment
    all reach a live cluster; the decisions under test -- what gets rolled out,
    when a redeploy is skipped, and what the active pointer ends up at -- do not.
    """
    contract = _contract()
    topology = _topology(contract)
    store = FilesystemRevisionStore(tmp_path / "store")
    spec = ProjectSpec(root=external_project, distribution_root=ROOT)
    manifest = project_revision.build_project_revision(
        spec, image=_IMAGE, distribution_version=distribution_version_at(ROOT)
    )
    project_revision.publish(store, manifest, spec)

    helm = _Helm()
    floe_calls: list[str] = []
    catalog_calls: list[str] = []

    monkeypatch.setattr(activation_module.contracts, "load_provider_contracts", lambda *a, **k: contract)
    image_calls: list[str] = []
    monkeypatch.setattr(
        activation_module, "_ensure_image", lambda provider, image, **k: image_calls.append(image)
    )
    monkeypatch.setattr(activation_module, "prepare_chart", lambda *a, **k: None)
    monkeypatch.setattr(activation_module, "deploy_optional_layer_artifacts", lambda *a, **k: None)
    monkeypatch.setattr(activation_module, "_user_chart", lambda chart, work_root: work_root)
    monkeypatch.setattr(activation_module, "sync_catalog_namespaces", lambda: catalog_calls.append("sync"))

    def _floe(provider, *, repo_root, contract_environ, env):  # noqa: ANN001, ANN202, ARG001
        floe_calls.append(str(repo_root))
        return _FLOE

    monkeypatch.setattr(activation_module, "_generate_floe", _floe)

    @contextmanager
    def _contract_environment(**kwargs):  # noqa: ANN003, ANN202
        yield {"OPENLAKEFORGE_KUBE_NAMESPACE": kwargs["namespace"]}

    monkeypatch.setattr(activation_module.contract_env, "applied_contract_environment", _contract_environment)

    providers: list[_Provider] = []

    def deploy(stage: str):  # noqa: ANN202
        context = DeploymentContext.local(
            repo_root=external_project, topology=topology, stage=stage, work_root=tmp_path / "work"
        )
        context.paths.work_root.mkdir(parents=True, exist_ok=True)
        provider = _Provider(context, helm)
        if providers:
            provider.config.floe = providers[-1].config.floe
        providers.append(provider)
        return activation_module.deploy_revision(
            provider, revision=manifest.revision, store=store, profile_name="acceptance"
        )

    return SimpleNamespace(
        deploy=deploy,
        store=store,
        helm=helm,
        manifest=manifest,
        floe_calls=floe_calls,
        image_calls=image_calls,
        providers=providers,
        catalog_calls=catalog_calls,
    )


def test_reapplying_the_active_revision_changes_nothing(harness) -> None:  # noqa: ANN001
    first = harness.deploy("dev")
    second = harness.deploy("dev")

    assert second == first
    assert len(harness.helm.rollouts) == 1
    # The point of the early gate: a no-op redeploy must not re-render Floe or
    # reconcile catalog namespaces just to discover it had nothing to do.
    assert len(harness.floe_calls) == 1
    assert len(harness.catalog_calls) == 1
    # Pulling the image (and on local, reloading it into kind) is a cluster
    # mutation, so it belongs after the gate, not before it.
    assert len(harness.image_calls) == 1


def test_promoting_one_revision_to_another_stage_reuses_the_project_revision(harness) -> None:  # noqa: ANN001
    dev = harness.deploy("dev")
    prod = harness.deploy("prod")

    assert dev.project_revision == prod.project_revision == harness.manifest.revision
    assert dev.activation_revision != prod.activation_revision
    assert project_activation.active(harness.store, stage=StageName.DEV) == dev
    assert project_activation.active(harness.store, stage=StageName.PROD) == prod
    assert {namespace for namespace, _ in harness.helm.rollouts} == {"olf-dev", "olf-prod"}


def test_failed_rollout_leaves_the_previous_revision_active(harness) -> None:  # noqa: ANN001
    active = harness.deploy("dev")
    harness.helm.fails = True

    with pytest.raises(RuntimeError):
        harness.deploy("prod")

    assert project_activation.active(harness.store, stage=StageName.DEV) == active
    assert project_activation.active(harness.store, stage=StageName.PROD) is None


def test_rollout_inherits_the_platform_release_globals(harness) -> None:  # noqa: ANN001
    """The subchart installed standalone would otherwise default to a secret name no stage uses."""
    harness.deploy("prod")

    _, values = harness.helm.rollouts[0]

    assert values["global"]["postgresqlSecretName"] == "postgresql-dagster-olf-prod-creds"


def test_rollout_carries_the_activated_image_into_the_log_archiver(harness) -> None:  # noqa: ANN001
    activation = harness.deploy("dev")

    _, values = harness.helm.rollouts[0]
    archiver = values["extraManifests"][0]
    container = archiver["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]

    assert archiver["kind"] == "CronJob"
    assert container["image"] == activation.project_code_image
    assert archiver["metadata"]["namespace"] == "olf-dev"


def test_reapplying_repairs_a_release_that_drifted_to_another_activation(harness) -> None:  # noqa: ANN001
    """A healthy-but-wrong release must not be mistaken for an idempotent no-op."""
    activation = harness.deploy("dev")
    drifted = harness.helm.installed["olf-dev"]
    drifted["deployments"][0]["deploymentLabels"]["openlakeforge.io/activation-revision"] = "sha256:" + "9" * 64

    reapplied = harness.deploy("dev")

    assert reapplied == activation
    assert len(harness.helm.rollouts) == 2


def test_rollout_keeps_contract_runtime_aliases(external_project: Path, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    """Non-prefixed aliases reach the pod: libs.s3_artifacts reads AWS_ENDPOINT_URL_S3 itself."""
    from olf.deployment.activation import _user_values
    from olf.project_activation import ProjectActivation

    activation = ProjectActivation(
        deployment_profile="acceptance",
        provider="local",
        stage=StageName.DEV,
        project_name="demo",
        project_revision="sha256:" + "b" * 64,
        distribution_version="0.0.0",
        project_code_image=_IMAGE,
        floe_manifest_revision=_FLOE,
        provider_binding_digest="sha256:" + "d" * 64,
        capabilities={"analytics": False, "governance": False},
    ).resolved()

    values = _user_values(
        activation,
        contract_environ={
            "AWS_ENDPOINT_URL_S3": "http://seaweedfs-s3.olf-system:8333",
            "AWS_REGION": "us-east-1",
            "AWS_SECRET_ACCESS_KEY": "must-not-appear",
            "OPENLAKEFORGE_STORAGE_BUCKET": "lakehouse-bronze",
        },
        namespace="olf-dev",
        platform_globals={},
        floe_renderer="ghcr.io/malon64/floe:0.6.11|0.6.11|image",
    )

    env = {entry["name"]: entry["value"] for entry in values["deployments"][0]["env"]}
    assert env["AWS_ENDPOINT_URL_S3"] == "http://seaweedfs-s3.olf-system:8333"
    assert env["AWS_REGION"] == "us-east-1"
    assert env["OPENLAKEFORGE_STORAGE_BUCKET"] == "lakehouse-bronze"
    # Credential values travel by Secret reference, never inline.
    assert "AWS_SECRET_ACCESS_KEY" not in env


def _values(**capabilities: bool) -> dict:
    from olf.deployment.activation import _user_values
    from olf.project_activation import ProjectActivation

    activation = ProjectActivation(
        deployment_profile="acceptance",
        provider="local",
        stage=StageName.DEV,
        project_name="demo",
        project_revision="sha256:" + "b" * 64,
        distribution_version="0.0.0",
        project_code_image=_IMAGE,
        floe_manifest_revision=_FLOE,
        provider_binding_digest="sha256:" + "d" * 64,
        capabilities={"analytics": False, "governance": False, **capabilities},
    ).resolved()
    return _user_values(
        activation,
        contract_environ={
            "OPENLAKEFORGE_LOG_BASE_URI": "s3://ops/activations/dev/logs",
            "AWS_ENDPOINT_URL_S3": "http://seaweedfs-s3.olf-system:8333",
            "OPENLAKEFORGE_STORAGE_CREDENTIALS_SECRET_NAME": "seaweedfs-s3-creds",
            "OPENLAKEFORGE_CATALOG_FLOE_CREDENTIALS_SECRET_NAME": "polaris-floe-creds",
            "OPENLAKEFORGE_GOVERNANCE_INGESTION_BOT_SECRET_NAME": "openmetadata-ingestion-bot",
            "OPENLAKEFORGE_GOVERNANCE_INGESTION_BOT_JWT_KEY": "OPENMETADATA_INGESTION_BOT_JWT",
        },
        namespace="olf-dev",
        platform_globals={},
        floe_renderer="ghcr.io/malon64/floe:0.6.11|0.6.11|image",
    )


def test_governed_stage_maps_the_bot_jwt_to_the_name_openlineage_reads() -> None:
    """Mounting the Secret alone would expose its own key name, not OPENLINEAGE_API_KEY."""
    entry = next(e for e in _values(governance=True)["deployments"][0]["env"] if e["name"] == "OPENLINEAGE_API_KEY")

    assert entry["valueFrom"]["secretKeyRef"] == {
        "name": "openmetadata-ingestion-bot",
        "key": "OPENMETADATA_INGESTION_BOT_JWT",
    }


def test_ungoverned_stage_maps_no_lineage_key() -> None:
    assert not [e for e in _values()["deployments"][0]["env"] if e["name"] == "OPENLINEAGE_API_KEY"]


def test_log_archiver_only_receives_storage_credentials() -> None:
    """It reads the object store and nothing else, so it should reach nothing else."""
    values = _values(governance=True)
    archiver = values["extraManifests"][0]["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]

    assert [ref["secretRef"]["name"] for ref in archiver["envFrom"]] == ["seaweedfs-s3-creds"]
    # The governance JWT reaches the code server, never the log container.
    archive_env = {entry["name"] for entry in archiver["env"]}
    assert "OPENLINEAGE_API_KEY" not in archive_env
    assert archive_env == {
        "OPENLAKEFORGE_LOG_BASE_URI",
        "AWS_ENDPOINT_URL_S3",
        "OPENLAKEFORGE_KUBE_NAMESPACE",
        "OPENLAKEFORGE_LOG_ARCHIVE_SINCE_SECONDS",
    }
    # The code server still gets the full set it needs.
    assert {s["name"] for s in values["deployments"][0]["envSecrets"]} == {
        "seaweedfs-s3-creds",
        "polaris-floe-creds",
        "openmetadata-ingestion-bot",
    }


def test_namespace_alias_follows_the_activated_stage() -> None:
    """floe_dagster.kubernetes_runner submits Jobs into NAMESPACE, defaulting to "lakehouse"."""
    env = {entry["name"]: entry.get("value") for entry in _values()["deployments"][0]["env"]}

    assert env["NAMESPACE"] == "olf-dev"


def test_reapplying_reconciles_globals_the_platform_rebound(harness) -> None:  # noqa: ANN001
    """A platform apply can rename the credentials Secret without moving any activation input."""
    activation = harness.deploy("dev")
    installed = harness.helm.installed["olf-dev"]
    assert installed["global"] == {"postgresqlSecretName": "postgresql-dagster-olf-dev-creds"}

    harness.helm.secret_suffix = "rotated"
    reapplied = harness.deploy("dev")

    assert reapplied == activation
    assert len(harness.helm.rollouts) == 2
    assert harness.helm.installed["olf-dev"]["global"] == {
        "postgresqlSecretName": "postgresql-dagster-olf-dev-rotated"
    }


def test_reapplying_regenerates_when_the_floe_renderer_changes(harness) -> None:  # noqa: ANN001
    """FLOE_IMAGE/VERSION/RUNTIME change the manifests without moving any activation input."""
    harness.deploy("dev")

    harness.providers[-1].config.floe = SimpleNamespace(
        image="ghcr.io/malon64/floe:0.7.0", version="0.7.0", runtime="image"
    )
    harness.deploy("dev")

    assert len(harness.helm.rollouts) == 2
    annotations = harness.helm.installed["olf-dev"]["deployments"][0]["deploymentAnnotations"]
    assert annotations["openlakeforge.io/floe-renderer"] == "ghcr.io/malon64/floe:0.7.0|0.7.0|image"
