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
        self.fails = False

    def status(self, release: str, *, namespace: str, kube_context=None, env=None):  # noqa: ANN001, ANN202, ARG002
        return SimpleNamespace(ok=namespace in self.ready)

    def upgrade_install(self, release, chart, *, namespace, values, kube_context=None, env=None):  # noqa: ANN001, ANN202, ARG002
        if self.fails:
            raise RuntimeError("helm upgrade --atomic rolled back")
        self.rollouts.append((namespace, yaml.safe_load(values.read_text())))
        self.ready.add(namespace)

    def uninstall(self, release, *, namespace, kube_context=None, env=None) -> None:  # noqa: ANN001, ARG002
        self.uninstalled.append(namespace)
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
    monkeypatch.setattr(activation_module, "_ensure_image", lambda *a, **k: None)
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

    def deploy(stage: str):  # noqa: ANN202
        context = DeploymentContext.local(
            repo_root=external_project, topology=topology, stage=stage, work_root=tmp_path / "work"
        )
        context.paths.work_root.mkdir(parents=True, exist_ok=True)
        return activation_module.deploy_revision(
            _Provider(context, helm), revision=manifest.revision, store=store, profile_name="acceptance"
        )

    return SimpleNamespace(
        deploy=deploy,
        store=store,
        helm=helm,
        manifest=manifest,
        floe_calls=floe_calls,
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


def test_rollout_carries_the_activated_image_into_the_log_archiver(harness) -> None:  # noqa: ANN001
    activation = harness.deploy("dev")

    _, values = harness.helm.rollouts[0]
    archiver = values["extraManifests"][0]
    container = archiver["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]

    assert archiver["kind"] == "CronJob"
    assert container["image"] == activation.project_code_image
    assert archiver["metadata"]["namespace"] == "olf-dev"
