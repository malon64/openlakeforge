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


def test_applied_authentication_environment_exposes_and_restores_olf_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OLF_HOME", "/original")
    monkeypatch.setenv("AWS_PROFILE", "ambient")

    with artifacts._applied_authentication_environment(
        "aws", {"OLF_HOME": "/selected", "AWS_PROFILE": "olf-sso"}
    ):
        assert os.environ["OLF_HOME"] == "/selected"
        assert os.environ["AWS_PROFILE"] == "olf-sso"

    assert os.environ["OLF_HOME"] == "/original"
    assert os.environ["AWS_PROFILE"] == "ambient"


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
        "deploy_optional_layers",
        "set_image",
    ]


def test_artifacts_deploy_does_not_switch_dagster_image_when_optional_layers_fail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The removed cloud deploy-artifacts.sh ran deploy-optional-layers
    before set-project-code-image, so a failed Superset/OpenMetadata deploy
    left the running Dagster deployment unchanged. Preserve that failure
    boundary.
    """
    config = _config(tmp_path)
    tools = _toolkit()
    backend = FakeCloudBackend(scope="aws", transport="direct")
    set_image_called = False

    monkeypatch.setattr(artifacts.contract_env, "applied_contract_environment", _fake_contract_env(lambda: None))
    monkeypatch.setattr(artifacts, "sync_catalog_namespaces", lambda: None)
    monkeypatch.setattr(artifacts, "activate_runtime_revision", lambda root, *, via: "sha256:abc")
    monkeypatch.setattr(artifacts, "build_and_push_project_code_image", lambda *a, **k: None)
    monkeypatch.setattr(artifacts, "upload_runtime_manifests", lambda root, *, via: None)

    def _raise(environ):  # noqa: ANN001, ARG001
        raise RuntimeError("optional layer deploy failed")

    monkeypatch.setattr(artifacts, "deploy_optional_layer_artifacts", _raise)

    def _set_image(image, namespace):  # noqa: ANN001, ARG001
        nonlocal set_image_called
        set_image_called = True

    monkeypatch.setattr(artifacts.k8s, "set_project_code_image", _set_image)
    backend.generate_floe_manifests = lambda *a, **k: []

    with pytest.raises(RuntimeError, match="optional layer deploy failed"):
        artifacts.artifacts_deploy(config, tools, backend, _FACTS, env={})

    assert set_image_called is False


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

    def _fake_generate(  # noqa: ANN001
        cfg, tls, *, repo_root, distribution_root, namespace, governance_enabled, environ, env
    ):
        seen_context["kube_context"] = environ.get("KUBE_CONTEXT")
        return []

    backend.generate_floe_manifests = _fake_generate

    artifacts.artifacts_deploy(config, tools, backend, _FACTS, env={})

    assert seen_context["kube_context"] == _FACTS.kube_context


def test_artifacts_deploy_honors_contract_terraform_dir_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR` must win over the provider's
    default platform Terraform directory, matching the removed
    `scripts/{aws,azure}/stack/deploy-artifacts.sh` and `olf.e2e._runner`.
    """
    config = _config(tmp_path)
    tools = _toolkit()
    backend = FakeCloudBackend(scope="aws")
    override_dir = tmp_path / "custom/contract-env"
    override_dir.mkdir(parents=True)
    monkeypatch.setenv("OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR", str(override_dir))

    seen_dirs: list[Path] = []

    @contextmanager
    def _capturing_contract_env(*, contract_terraform_dir, **kwargs):  # noqa: ANN001, ANN003
        seen_dirs.append(contract_terraform_dir)
        yield dict(os.environ)

    monkeypatch.setattr(artifacts.contract_env, "applied_contract_environment", _capturing_contract_env)
    monkeypatch.setattr(artifacts, "sync_catalog_namespaces", lambda: None)
    monkeypatch.setattr(artifacts, "activate_runtime_revision", lambda root, *, via: "sha256:abc")
    monkeypatch.setattr(artifacts, "build_and_push_project_code_image", lambda *a, **k: None)
    monkeypatch.setattr(artifacts, "upload_runtime_manifests", lambda root, *, via: None)
    monkeypatch.setattr(artifacts.k8s, "set_project_code_image", lambda image, namespace: None)
    monkeypatch.setattr(artifacts, "deploy_optional_layer_artifacts", lambda environ: None)
    backend.generate_floe_manifests = lambda *a, **k: []

    artifacts.artifacts_deploy(config, tools, backend, _FACTS, env={})

    assert seen_dirs == [override_dir]


def test_artifacts_deploy_falls_back_to_platform_terraform_dir_when_override_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    tools = _toolkit()
    backend = FakeCloudBackend(scope="aws")
    monkeypatch.delenv("OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR", raising=False)

    seen_dirs: list[Path] = []

    @contextmanager
    def _capturing_contract_env(*, contract_terraform_dir, **kwargs):  # noqa: ANN001, ANN003
        seen_dirs.append(contract_terraform_dir)
        yield dict(os.environ)

    monkeypatch.setattr(artifacts.contract_env, "applied_contract_environment", _capturing_contract_env)
    monkeypatch.setattr(artifacts, "sync_catalog_namespaces", lambda: None)
    monkeypatch.setattr(artifacts, "activate_runtime_revision", lambda root, *, via: "sha256:abc")
    monkeypatch.setattr(artifacts, "build_and_push_project_code_image", lambda *a, **k: None)
    monkeypatch.setattr(artifacts, "upload_runtime_manifests", lambda root, *, via: None)
    monkeypatch.setattr(artifacts.k8s, "set_project_code_image", lambda image, namespace: None)
    monkeypatch.setattr(artifacts, "deploy_optional_layer_artifacts", lambda environ: None)
    backend.generate_floe_manifests = lambda *a, **k: []

    artifacts.artifacts_deploy(config, tools, backend, _FACTS, env={})

    assert seen_dirs == [config.paths.platform_terraform_dir]
