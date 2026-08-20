from __future__ import annotations

import os
from pathlib import Path

from olf.deployment.context import DeploymentContext, Profile, Provider


def test_local_defaults(tmp_path: Path) -> None:
    ctx = DeploymentContext.local(repo_root=tmp_path)

    assert ctx.provider == Provider.LOCAL
    assert ctx.namespace == "lakehouse"
    assert ctx.kube_context == "kind-openlakeforge-local"
    assert ctx.paths.kubeconfig_path == tmp_path / ".tmp" / "kubeconfigs" / "local.yaml"
    assert ctx.paths.platform_terraform_dir == tmp_path / "infra" / "terraform" / "environments" / "local"
    assert ctx.paths.foundation_terraform_dir == tmp_path / "infra" / "terraform" / "foundations" / "local-kind"
    assert ctx.paths.docker_config_dir == tmp_path / ".tmp" / "docker" / "local"
    assert ctx.paths.helm_repository_config == tmp_path / ".tmp" / "helm" / "local" / "repositories.yaml"
    assert ctx.paths.helm_repository_cache == tmp_path / ".tmp" / "helm" / "local" / "repository-cache"


def test_local_kubeconfig_path_override(tmp_path: Path) -> None:
    override = tmp_path / "custom" / "kind-smoke.yaml"

    ctx = DeploymentContext.local(repo_root=tmp_path, kubeconfig_path=override)

    assert ctx.paths.kubeconfig_path == override
    # Only the kubeconfig path changes; every other derived path is untouched.
    assert ctx.paths.docker_config_dir == tmp_path / ".tmp" / "docker" / "local"
    assert ctx.paths.helm_repository_config == tmp_path / ".tmp" / "helm" / "local" / "repositories.yaml"


def test_local_kubeconfig_path_override_is_resolved_to_an_absolute_path(tmp_path: Path) -> None:
    ctx = DeploymentContext.local(repo_root=tmp_path, kubeconfig_path=Path("relative/kubeconfig.yaml"))

    assert ctx.paths.kubeconfig_path.is_absolute()


def test_aws_and_azure_produce_independent_scoped_paths(tmp_path: Path) -> None:
    aws_ctx = DeploymentContext.aws(repo_root=tmp_path)
    azure_ctx = DeploymentContext.azure(repo_root=tmp_path)

    assert aws_ctx.paths.kubeconfig_path != azure_ctx.paths.kubeconfig_path
    assert aws_ctx.paths.docker_config_dir != azure_ctx.paths.docker_config_dir
    assert aws_ctx.paths.helm_repository_config != azure_ctx.paths.helm_repository_config
    assert aws_ctx.paths.platform_terraform_dir == tmp_path / "infra" / "terraform" / "environments" / "aws-poc"
    assert azure_ctx.paths.platform_terraform_dir == tmp_path / "infra" / "terraform" / "environments" / "azure-poc"


def test_for_provider_dispatches_to_matching_factory(tmp_path: Path) -> None:
    ctx = DeploymentContext.for_provider("aws", repo_root=tmp_path)
    assert ctx.provider == Provider.AWS

    ctx = DeploymentContext.for_provider(Provider.AZURE, repo_root=tmp_path)
    assert ctx.provider == Provider.AZURE


def test_slim_profile_disables_optional_layers(tmp_path: Path) -> None:
    ctx = DeploymentContext.local(repo_root=tmp_path, profile=Profile.SLIM)

    assert ctx.features.governance_enabled is False
    assert ctx.features.analytics_enabled is False


def test_full_profile_enables_optional_layers(tmp_path: Path) -> None:
    ctx = DeploymentContext.local(repo_root=tmp_path, profile=Profile.FULL)

    assert ctx.features.governance_enabled is True
    assert ctx.features.analytics_enabled is True


def test_repo_root_and_distribution_root_can_differ(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    distribution_root = tmp_path / "distribution"
    repo_root.mkdir()
    distribution_root.mkdir()

    ctx = DeploymentContext.local(repo_root=repo_root, distribution_root=distribution_root)

    assert ctx.paths.repo_root == repo_root.resolve()
    assert ctx.paths.distribution_root == distribution_root.resolve()
    assert ctx.paths.repo_root != ctx.paths.distribution_root


def test_distribution_root_defaults_to_repo_root(tmp_path: Path) -> None:
    ctx = DeploymentContext.local(repo_root=tmp_path)

    assert ctx.paths.distribution_root == ctx.paths.repo_root


def test_command_env_sets_expected_keys_without_mutating_parent(tmp_path: Path) -> None:
    ctx = DeploymentContext.local(repo_root=tmp_path)
    assert "KUBECONFIG" not in os.environ

    env = ctx.command_env(docker_host="unix:///var/run/docker.sock")

    assert env["KUBECONFIG"] == str(ctx.paths.kubeconfig_path)
    assert env["DOCKER_CONFIG"] == str(ctx.paths.docker_config_dir)
    assert env["DOCKER_HOST"] == "unix:///var/run/docker.sock"
    assert env["DOCKER_BUILDKIT"] == "1"
    assert env["BUILDKIT_PROGRESS"] == "plain"
    assert env["HELM_REPOSITORY_CONFIG"] == str(ctx.paths.helm_repository_config)
    assert env["HELM_REPOSITORY_CACHE"] == str(ctx.paths.helm_repository_cache)
    assert "KUBECONFIG" not in os.environ


def test_command_env_without_docker_host_omits_it(tmp_path: Path) -> None:
    ctx = DeploymentContext.local(repo_root=tmp_path)

    env = ctx.command_env()

    assert "DOCKER_HOST" not in env


def test_command_env_does_not_mutate_supplied_base_mapping(tmp_path: Path) -> None:
    ctx = DeploymentContext.local(repo_root=tmp_path)
    base = {"PATH": "/usr/bin", "DOCKER_BUILDKIT": "0"}
    base_copy = dict(base)

    env = ctx.command_env(base=base)

    assert base == base_copy
    assert env["DOCKER_BUILDKIT"] == "0"  # base overrides default when caller pre-sets it
    assert env["PATH"] == "/usr/bin"


def test_prepare_directories_creates_owned_paths(tmp_path: Path) -> None:
    ctx = DeploymentContext.local(repo_root=tmp_path)

    ctx.prepare_directories(docker_cli_plugins_source=tmp_path / "no-such-docker-home")

    assert ctx.paths.kubeconfig_path.parent.is_dir()
    assert ctx.paths.docker_config_dir.is_dir()
    assert ctx.paths.helm_repository_config.parent.is_dir()
    assert ctx.paths.helm_repository_cache.is_dir()
    assert ctx.paths.helm_cache_dir.is_dir()
    assert ctx.paths.superset_report_work_dir.is_dir()


def test_prepare_directories_links_docker_cli_plugins_when_source_exists(tmp_path: Path) -> None:
    docker_home = tmp_path / "docker-home"
    plugins_source = docker_home / "cli-plugins"
    plugins_source.mkdir(parents=True)
    (plugins_source / "buildx").write_text("fake plugin")

    ctx = DeploymentContext.local(repo_root=tmp_path)
    ctx.prepare_directories(docker_cli_plugins_source=docker_home)

    destination = ctx.paths.docker_config_dir / "cli-plugins"
    assert destination.is_symlink()
    assert destination.resolve() == plugins_source.resolve()


def test_prepare_directories_does_not_overwrite_existing_cli_plugins_link(tmp_path: Path) -> None:
    docker_home = tmp_path / "docker-home"
    plugins_source = docker_home / "cli-plugins"
    plugins_source.mkdir(parents=True)

    ctx = DeploymentContext.local(repo_root=tmp_path)
    ctx.paths.docker_config_dir.mkdir(parents=True)
    existing_destination = ctx.paths.docker_config_dir / "cli-plugins"
    existing_destination.mkdir()
    marker = existing_destination / "already-here"
    marker.write_text("do not touch")

    ctx.prepare_directories(docker_cli_plugins_source=docker_home)

    assert marker.exists()
    assert not existing_destination.is_symlink()


def test_prepare_directories_no_failure_when_source_missing(tmp_path: Path) -> None:
    ctx = DeploymentContext.local(repo_root=tmp_path)

    ctx.prepare_directories(docker_cli_plugins_source=tmp_path / "does-not-exist")

    assert not (ctx.paths.docker_config_dir / "cli-plugins").exists()
