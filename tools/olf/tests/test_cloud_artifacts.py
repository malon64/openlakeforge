from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

import pytest
from _cloud_support import FakeCloudBackend

from olf import contracts as contracts_module
from olf.deployment.cloud import artifacts
from olf.deployment.cloud.backend import FoundationFacts
from olf.deployment.cloud.config import CloudDeploymentConfig
from olf.deployment.context import DeploymentContext
from olf.deployment.engine import Toolkit

_FACTS = FoundationFacts(
    cluster_name="eks-openlakeforge-poc",
    kube_context="eks-openlakeforge-poc",
    project_code_repository="123.dkr.ecr.eu-west-1.amazonaws.com/project-code",
    superset_repository="123.dkr.ecr.eu-west-1.amazonaws.com/superset",
    aws_region="eu-west-1",
)


def _config(tmp_path: Path) -> CloudDeploymentConfig:
    context = DeploymentContext.aws(repo_root=tmp_path)
    return CloudDeploymentConfig.from_environment({}, context=context)


def _toolkit() -> Toolkit:
    return Toolkit.default(overrides={t: Path(t) for t in ("terraform", "docker", "kind", "kubectl", "helm")})


def _fake_contract_env(on_enter):  # noqa: ANN001, ANN202
    @contextmanager
    def _cm(**kwargs):  # noqa: ANN003
        on_enter()
        yield dict(os.environ)

    return _cm


def test_artifacts_deploy_preserves_step_order(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = _config(tmp_path)
    tools = _toolkit()
    backend = FakeCloudBackend(scope="aws", transport="direct")
    calls: list[str] = []

    monkeypatch.setattr(
        artifacts.contract_env, "applied_contract_environment", _fake_contract_env(lambda: calls.append("contract_env"))
    )
    monkeypatch.setattr(artifacts, "sync_catalog_namespaces", lambda: calls.append("sync_namespaces"))
    monkeypatch.setattr(
        artifacts,
        "activate_runtime_revision",
        lambda root, *, via: calls.append(f"activate({via})") or "sha256:abc",
    )
    monkeypatch.setattr(
        artifacts,
        "build_and_push_project_code_image",
        lambda cfg, tls, bk, fc, *, env, revision: calls.append(f"build_push(revision={revision})"),
    )
    monkeypatch.setattr(
        artifacts, "upload_runtime_manifests", lambda root, *, via: calls.append(f"upload({via})")
    )
    monkeypatch.setattr(artifacts.k8s, "set_project_code_image", lambda image, namespace: calls.append("set_image"))
    monkeypatch.setattr(
        artifacts, "deploy_optional_layer_artifacts", lambda environ: calls.append("deploy_optional_layers")
    )

    def _fake_generate(*a, **k):  # noqa: ANN002, ANN003
        calls.append("generate_manifests")
        return []

    backend.generate_floe_manifests = _fake_generate

    artifacts.artifacts_deploy(config, tools, backend, _FACTS, env={})

    assert calls == [
        "contract_env",
        "sync_namespaces",
        "generate_manifests",
        "activate(direct)",
        "build_push(revision=sha256:abc)",
        "upload(direct)",
        "set_image",
        "deploy_optional_layers",
    ]


def test_artifacts_deploy_passes_backend_transport_to_artifact_steps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _config(tmp_path)
    tools = _toolkit()
    backend = FakeCloudBackend(scope="azure", transport="port-forward")
    seen_via: list[str] = []

    monkeypatch.setattr(
        artifacts.contract_env, "applied_contract_environment", _fake_contract_env(lambda: None)
    )
    monkeypatch.setattr(artifacts, "sync_catalog_namespaces", lambda: None)
    monkeypatch.setattr(
        artifacts,
        "activate_runtime_revision",
        lambda root, *, via: seen_via.append(("activate", via)) or "sha256:abc",
    )
    monkeypatch.setattr(artifacts, "build_and_push_project_code_image", lambda *a, **k: None)
    monkeypatch.setattr(
        artifacts, "upload_runtime_manifests", lambda root, *, via: seen_via.append(("upload", via))
    )
    monkeypatch.setattr(artifacts.k8s, "set_project_code_image", lambda image, namespace: None)
    monkeypatch.setattr(artifacts, "deploy_optional_layer_artifacts", lambda environ: None)
    backend.generate_floe_manifests = lambda *a, **k: []

    artifacts.artifacts_deploy(config, tools, backend, _FACTS, env={})

    assert seen_via == [("activate", "port-forward"), ("upload", "port-forward")]


def test_applied_contract_environment_uses_facts_kube_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    tools = _toolkit()
    backend = FakeCloudBackend(scope="aws")
    monkeypatch.setattr(contracts_module, "load_provider_contracts", lambda terraform_dir: None)
    monkeypatch.setattr(
        contracts_module, "build_contract_env", lambda base, contracts_value, *, repo_root: ({}, [])
    )
    monkeypatch.setattr(artifacts, "sync_catalog_namespaces", lambda: None)
    monkeypatch.setattr(artifacts, "activate_runtime_revision", lambda root, *, via: "sha256:abc")
    monkeypatch.setattr(artifacts, "build_and_push_project_code_image", lambda *a, **k: None)
    monkeypatch.setattr(artifacts, "upload_runtime_manifests", lambda root, *, via: None)
    monkeypatch.setattr(artifacts.k8s, "set_project_code_image", lambda image, namespace: None)
    monkeypatch.setattr(artifacts, "deploy_optional_layer_artifacts", lambda environ: None)

    seen_context = {}

    def _fake_generate(cfg, tls, *, repo_root, namespace, governance_enabled, environ, env):  # noqa: ANN001
        seen_context["kube_context"] = environ.get("KUBE_CONTEXT")
        return []

    backend.generate_floe_manifests = _fake_generate

    artifacts.artifacts_deploy(config, tools, backend, _FACTS, env={})

    assert seen_context["kube_context"] == _FACTS.kube_context
