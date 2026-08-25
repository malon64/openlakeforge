"""Typed deployment context: paths, provider identity, and scoped environment.

This is deliberately separate from `olf.config` (the lightweight runtime
contract/environment helper); `DeploymentContext` is the new-code home for
provider/profile-scoped path and environment construction. It never executes
subprocesses and never mutates `os.environ` — building it is pure data
assembly, and command execution stays owned by `olf.tooling`.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path

DEFAULT_NAMESPACE = "lakehouse"
DEFAULT_LOCAL_CLUSTER_NAME = "openlakeforge-local"


def _resolve_foundation_state_path(foundation_terraform_dir: Path) -> Path:
    """Honor `FOUNDATION_STATE_PATH`, matching every removed provider's
    stack scripts (`FOUNDATION_STATE_PATH="${FOUNDATION_STATE_PATH:-
    ${FOUNDATION_TERRAFORM_DIR}/terraform.tfstate}"`), which both gated
    their own state-missing precondition check on this path and supplied it
    as the `foundation_state_path` Terraform variable to the platform root -
    a non-default foundation state (e.g. a distribution's isolated backend)
    would otherwise be rejected by every platform/artifact/status/forward/
    teardown command.
    """
    override = os.environ.get("FOUNDATION_STATE_PATH")
    if override:
        return Path(override).resolve()
    return foundation_terraform_dir / "terraform.tfstate"


class Provider(StrEnum):
    LOCAL = "local"
    AWS = "aws"
    AZURE = "azure"


class Profile(StrEnum):
    SLIM = "slim"
    FULL = "full"


@dataclass(frozen=True)
class DeploymentPaths:
    repo_root: Path
    distribution_root: Path
    state_root: Path
    work_root: Path
    cache_root: Path
    kubeconfig_path: Path
    foundation_terraform_dir: Path
    platform_terraform_dir: Path
    foundation_state_path: Path
    platform_state_path: Path
    terraform_data_root: Path
    docker_config_dir: Path
    helm_cache_dir: Path
    helm_repository_config: Path
    helm_repository_cache: Path
    superset_report_work_dir: Path
    port_forward_log_prefix: Path


@dataclass(frozen=True)
class DeploymentFeatures:
    governance_enabled: bool
    analytics_enabled: bool

    @classmethod
    def for_profile(cls, profile: Profile) -> DeploymentFeatures:
        enabled = profile == Profile.FULL
        return cls(governance_enabled=enabled, analytics_enabled=enabled)


@dataclass(frozen=True)
class DeploymentContext:
    provider: Provider
    profile: Profile
    namespace: str
    kube_context: str
    paths: DeploymentPaths
    features: DeploymentFeatures

    @classmethod
    def for_provider(cls, provider: Provider | str, *, repo_root: Path, **kwargs: object) -> DeploymentContext:
        resolved = Provider(provider)
        factory = {
            Provider.LOCAL: cls.local,
            Provider.AWS: cls.aws,
            Provider.AZURE: cls.azure,
        }[resolved]
        return factory(repo_root=repo_root, **kwargs)  # type: ignore[arg-type]

    @classmethod
    def local(
        cls,
        *,
        repo_root: Path,
        profile: Profile = Profile.FULL,
        distribution_root: Path | None = None,
        state_root: Path | None = None,
        work_root: Path | None = None,
        cache_root: Path | None = None,
        namespace: str = DEFAULT_NAMESPACE,
        cluster_name: str = DEFAULT_LOCAL_CLUSTER_NAME,
        kubeconfig_path: Path | None = None,
    ) -> DeploymentContext:
        """Build the local `DeploymentContext`.

        `kubeconfig_path`, when given, overrides the default
        `<repo_root>/.tmp/kubeconfigs/local.yaml` - CI runs multiple local
        kind deployments in parallel (e.g. a slim smoke job alongside a full
        deployment) and isolates each with its own kubeconfig file via
        `LOCAL_KUBECONFIG_PATH`; the shell scripts this replaces honored the
        same override.
        """
        context = cls._build(
            provider=Provider.LOCAL,
            scope="local",
            repo_root=repo_root,
            distribution_root=distribution_root,
            state_root=state_root,
            work_root=work_root,
            cache_root=cache_root,
            profile=profile,
            namespace=namespace,
            kube_context=f"kind-{cluster_name}",
            foundation_terraform_dir=Path("infra/terraform/foundations/local-kind"),
            platform_terraform_dir=Path("infra/terraform/environments/local"),
        )
        if kubeconfig_path is not None:
            context = replace(context, paths=replace(context.paths, kubeconfig_path=Path(kubeconfig_path).resolve()))
        return context

    @classmethod
    def aws(
        cls,
        *,
        repo_root: Path,
        profile: Profile = Profile.FULL,
        distribution_root: Path | None = None,
        state_root: Path | None = None,
        work_root: Path | None = None,
        cache_root: Path | None = None,
        namespace: str = DEFAULT_NAMESPACE,
        kube_context: str = "",
        kubeconfig_path: Path | None = None,
    ) -> DeploymentContext:
        """Build the AWS `DeploymentContext`.

        `kubeconfig_path`, when given, overrides the default
        `<repo_root>/.tmp/kubeconfigs/aws.yaml` - the concurrent-deployment
        workflow in `docs/setup/cloud-poc-setup.md` isolates a parallel
        `make aws-up` run with its own kubeconfig file via
        `AWS_KUBECONFIG_PATH`, matching `local()`'s override.
        """
        context = cls._build(
            provider=Provider.AWS,
            scope="aws",
            repo_root=repo_root,
            distribution_root=distribution_root,
            state_root=state_root,
            work_root=work_root,
            cache_root=cache_root,
            profile=profile,
            namespace=namespace,
            kube_context=kube_context,
            foundation_terraform_dir=Path("infra/terraform/foundations/aws-eks"),
            platform_terraform_dir=Path("infra/terraform/environments/aws-poc"),
        )
        if kubeconfig_path is not None:
            context = replace(context, paths=replace(context.paths, kubeconfig_path=Path(kubeconfig_path).resolve()))
        return context

    @classmethod
    def azure(
        cls,
        *,
        repo_root: Path,
        profile: Profile = Profile.FULL,
        distribution_root: Path | None = None,
        state_root: Path | None = None,
        work_root: Path | None = None,
        cache_root: Path | None = None,
        namespace: str = DEFAULT_NAMESPACE,
        kube_context: str = "",
        kubeconfig_path: Path | None = None,
    ) -> DeploymentContext:
        """Build the Azure `DeploymentContext`.

        `kubeconfig_path`, when given, overrides the default
        `<repo_root>/.tmp/kubeconfigs/azure.yaml` - see `aws()`.
        """
        context = cls._build(
            provider=Provider.AZURE,
            scope="azure",
            repo_root=repo_root,
            distribution_root=distribution_root,
            state_root=state_root,
            work_root=work_root,
            cache_root=cache_root,
            profile=profile,
            namespace=namespace,
            kube_context=kube_context,
            foundation_terraform_dir=Path("infra/terraform/foundations/azure-aks"),
            platform_terraform_dir=Path("infra/terraform/environments/azure-poc"),
        )
        if kubeconfig_path is not None:
            context = replace(context, paths=replace(context.paths, kubeconfig_path=Path(kubeconfig_path).resolve()))
        return context

    @classmethod
    def _build(
        cls,
        *,
        provider: Provider,
        scope: str,
        repo_root: Path,
        distribution_root: Path | None,
        state_root: Path | None,
        work_root: Path | None,
        cache_root: Path | None,
        profile: Profile,
        namespace: str,
        kube_context: str,
        foundation_terraform_dir: Path,
        platform_terraform_dir: Path,
    ) -> DeploymentContext:
        resolved_repo_root = Path(repo_root).resolve()
        resolved_distribution_root = (
            Path(distribution_root).resolve() if distribution_root is not None else resolved_repo_root
        )
        resolved_work_root = (
            Path(work_root).resolve() / scope if work_root is not None else resolved_repo_root / ".tmp"
        )
        resolved_state_root = (Path(state_root).resolve() / scope) if state_root is not None else resolved_repo_root
        resolved_cache_root = Path(cache_root).resolve() if cache_root is not None else resolved_work_root
        helm_root = resolved_work_root / "helm" / scope
        foundation_dir = resolved_distribution_root / foundation_terraform_dir
        platform_dir = resolved_distribution_root / platform_terraform_dir
        foundation_state = (
            _resolve_foundation_state_path(foundation_dir)
            if state_root is None
            else Path(os.environ.get("FOUNDATION_STATE_PATH", resolved_state_root / "foundation.tfstate")).resolve()
        )

        paths = DeploymentPaths(
            repo_root=resolved_repo_root,
            distribution_root=resolved_distribution_root,
            state_root=resolved_state_root,
            work_root=resolved_work_root,
            cache_root=resolved_cache_root,
            kubeconfig_path=(
                resolved_state_root / "kubeconfig.yaml"
                if state_root is not None
                else resolved_work_root / "kubeconfigs" / f"{scope}.yaml"
            ),
            foundation_terraform_dir=foundation_dir,
            platform_terraform_dir=platform_dir,
            foundation_state_path=foundation_state,
            platform_state_path=(
                resolved_state_root / "platform.tfstate"
                if state_root is not None
                else platform_dir / "terraform.tfstate"
            ),
            terraform_data_root=resolved_work_root / "terraform-data",
            docker_config_dir=resolved_work_root / "docker" / scope,
            helm_cache_dir=(resolved_cache_root / "helm") if cache_root is not None else helm_root / "charts",
            helm_repository_config=helm_root / "repositories.yaml",
            helm_repository_cache=helm_root / "repository-cache",
            superset_report_work_dir=resolved_work_root / "superset-reports" / scope,
            port_forward_log_prefix=Path(f"/tmp/openlakeforge-{scope}"),
        )
        return cls(
            provider=provider,
            profile=profile,
            namespace=namespace,
            kube_context=kube_context,
            paths=paths,
            features=DeploymentFeatures.for_profile(profile),
        )

    def command_env(
        self,
        *,
        base: Mapping[str, str] | None = None,
        docker_host: str | None = None,
    ) -> dict[str, str]:
        """Build a command-scoped environment overlay.

        Does not mutate `os.environ` or any mapping passed in as `base`;
        returns a fresh dict a caller can pass to `ProcessRunner`/tool
        adapters as `env`.
        """
        env: dict[str, str] = dict(base) if base is not None else {}
        env["KUBECONFIG"] = str(self.paths.kubeconfig_path)
        env["KUBE_CONTEXT"] = self.kube_context
        env["DOCKER_CONFIG"] = str(self.paths.docker_config_dir)
        env.setdefault("DOCKER_BUILDKIT", "1")
        env.setdefault("BUILDKIT_PROGRESS", "plain")
        env["HELM_REPOSITORY_CONFIG"] = str(self.paths.helm_repository_config)
        env["HELM_REPOSITORY_CACHE"] = str(self.paths.helm_repository_cache)
        env["SUPERSET_REPORT_WORK_DIR"] = str(self.paths.superset_report_work_dir)
        env["OPENLAKEFORGE_PORT_FORWARD_LOG_PREFIX"] = str(self.paths.port_forward_log_prefix)
        # Fresh contract readers create their own managed-tool resolver. Keep
        # them anchored to the same verified distribution as the Toolkit.
        env["OLF_DISTRIBUTION_ROOT"] = str(self.paths.distribution_root)
        if self.paths.distribution_root != self.paths.repo_root:
            env["OPENLAKEFORGE_TERRAFORM_DATA_ROOT"] = str(self.paths.terraform_data_root)
            env["OPENLAKEFORGE_TERRAFORM_STATE_ROOT"] = str(self.paths.state_root)
            env["OPENLAKEFORGE_TERRAFORM_READONLY_LOCKFILE"] = "true"
        if docker_host:
            env["DOCKER_HOST"] = docker_host
        return env

    def prepare_directories(self, *, docker_cli_plugins_source: Path | None = None) -> None:
        """Create OpenLakeForge-owned working paths.

        Also links `~/.docker/cli-plugins` (or `$DOCKER_CONFIG/cli-plugins`)
        into the scoped Docker config directory, if present, so `docker build`
        keeps using BuildKit without an existing destination being overwritten.
        """
        for directory in (
            self.paths.kubeconfig_path.parent,
            self.paths.docker_config_dir,
            self.paths.helm_repository_config.parent,
            self.paths.helm_repository_cache,
            self.paths.helm_cache_dir,
            self.paths.superset_report_work_dir,
            self.paths.terraform_data_root,
        ):
            directory.mkdir(parents=True, exist_ok=True)

        self._link_docker_cli_plugins(source=docker_cli_plugins_source)

    def _link_docker_cli_plugins(self, *, source: Path | None) -> None:
        if source is None:
            docker_config_source = os.environ.get("DOCKER_CONFIG")
            source = Path(docker_config_source) if docker_config_source else Path.home() / ".docker"
        cli_plugins_source = source / "cli-plugins"
        destination = self.paths.docker_config_dir / "cli-plugins"

        if not cli_plugins_source.is_dir() or destination.exists():
            return
        destination.symlink_to(cli_plugins_source)
