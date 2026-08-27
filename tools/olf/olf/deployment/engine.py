"""Provider-neutral deployment orchestration seam.

`DeploymentEngine` sequences a `DeploymentProvider`'s lifecycle steps in the
order ADR 0002 requires (foundation -> platform -> dynamic artifacts, never
the other way around). `Toolkit` bundles the process-execution primitives
every provider needs, including the SDK-backed AWS and Azure adapters used by
the cloud provider (issue #125). `build_provider` is the single seam that
dispatches on `context.provider` - nothing provider-specific otherwise
belongs in this module.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from olf.deployment.context import DeploymentContext, Provider
from olf.deployment.errors import DeploymentPreconditionError, UnsupportedProviderError
from olf.deployment.inspection import DoctorReport, project_not_runnable_reason
from olf.tooling.aws import AwsSdk
from olf.tooling.azure import AzureSdk
from olf.tooling.docker import Docker
from olf.tooling.helm import Helm
from olf.tooling.kind import Kind
from olf.tooling.kubectl import Kubectl
from olf.tooling.process import ProcessRunner
from olf.tooling.resolver import ExecutableResolver, build_resolver
from olf.tooling.terraform import Terraform

if TYPE_CHECKING:
    from olf.deployment.status import StatusReport


class DeploymentPhase(StrEnum):
    ALL = "all"
    FOUNDATION = "foundation"
    PREFETCH = "prefetch"
    PLATFORM = "platform"
    ARTIFACTS = "artifacts"


@dataclass(frozen=True)
class Toolkit:
    """The process-execution primitives shared by every provider."""

    runner: ProcessRunner
    resolver: ExecutableResolver
    terraform: Terraform
    helm: Helm
    kubectl: Kubectl
    docker: Docker
    kind: Kind
    aws: AwsSdk
    azure: AzureSdk

    @classmethod
    def default(
        cls,
        *,
        log_commands: bool = False,
        overrides: Mapping[str, Path] | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> Toolkit:
        runner = ProcessRunner(log_commands=log_commands)
        resolver = build_resolver(overrides=overrides, environ=environ)
        return cls(
            runner=runner,
            resolver=resolver,
            terraform=Terraform(runner, resolver),
            helm=Helm(runner, resolver),
            kubectl=Kubectl(runner, resolver),
            docker=Docker(runner, resolver),
            kind=Kind(runner, resolver),
            aws=AwsSdk(),
            azure=AzureSdk(),
        )


class DeploymentProvider(Protocol):
    """A provider's granular lifecycle operations.

    Each method is the shared, directly-callable primitive behind both the
    combined `olf deploy`/`olf destroy` verbs and the granular Make target
    delegates (`--phase foundation`, `--phase platform`, ...) - nothing is
    duplicated between the two call paths.
    """

    context: DeploymentContext

    def foundation_up(self) -> None: ...
    def foundation_down(self, *, force: bool = False) -> None: ...
    def prepare_images(self) -> None: ...
    def platform_up(self) -> None: ...
    def platform_down(self) -> None: ...
    def artifacts_deploy(self) -> None: ...
    def status(self) -> StatusReport: ...
    def forward(self) -> None: ...
    def plan(self, phase: DeploymentPhase) -> bool: ...
    def doctor(self, phase: DeploymentPhase) -> DoctorReport: ...


@dataclass
class DeploymentEngine:
    """Sequences a provider's lifecycle steps for a given phase."""

    provider: DeploymentProvider
    _phase_deploy_order: tuple[DeploymentPhase, ...] = field(
        default=(
            DeploymentPhase.FOUNDATION,
            DeploymentPhase.PREFETCH,
            DeploymentPhase.PLATFORM,
            DeploymentPhase.ARTIFACTS,
        ),
        init=False,
        repr=False,
    )

    def deploy(self, phase: DeploymentPhase = DeploymentPhase.ALL) -> None:
        if phase in (DeploymentPhase.ALL, DeploymentPhase.ARTIFACTS):
            self._require_runnable_project()
        phases = self._phase_deploy_order if phase == DeploymentPhase.ALL else (phase,)
        for step in phases:
            self._deploy_step(step)

    def destroy(self, phase: DeploymentPhase = DeploymentPhase.ALL, *, force: bool = False) -> None:
        if phase == DeploymentPhase.ALL:
            self.provider.platform_down()
            self.provider.foundation_down(force=force)
            return
        if phase == DeploymentPhase.PLATFORM:
            self.provider.platform_down()
            return
        if phase == DeploymentPhase.FOUNDATION:
            self.provider.foundation_down(force=force)
            return
        raise ValueError(f"destroy does not support phase {phase!r}")

    def status(self) -> StatusReport:
        return self.provider.status()

    def forward(self) -> None:
        self.provider.forward()

    def plan(self, phase: DeploymentPhase = DeploymentPhase.ALL) -> bool:
        """Plan supported static phases and return whether Terraform found changes."""
        return self.provider.plan(phase)

    def doctor(self, phase: DeploymentPhase = DeploymentPhase.ALL) -> DoctorReport:
        return self.provider.doctor(phase)

    def _deploy_step(self, phase: DeploymentPhase) -> None:
        if phase == DeploymentPhase.FOUNDATION:
            self.provider.foundation_up()
        elif phase == DeploymentPhase.PREFETCH:
            self.provider.prepare_images()
        elif phase == DeploymentPhase.PLATFORM:
            self.provider.platform_up()
        elif phase == DeploymentPhase.ARTIFACTS:
            self.provider.artifacts_deploy()
        else:
            raise ValueError(f"deploy does not support phase {phase!r}")

    def _require_runnable_project(self) -> None:
        """Fail before a dynamic-artifact deploy mutates infrastructure.

        Static foundation/platform phases intentionally remain descriptor-free
        under ADR 0002, so only ARTIFACTS (and the ALL sequence that contains
        it) needs a runnable project. Reported here rather than deep inside an
        artifact step so an `olf init --empty` project is told what it is
        missing before anything is applied.
        """
        reason = project_not_runnable_reason(self.provider.context.paths.repo_root)
        if reason is not None:
            raise DeploymentPreconditionError(f"project is not runnable: {reason}")


def build_provider(
    context: DeploymentContext,
    *,
    toolkit: Toolkit | None = None,
    environ: Mapping[str, str] | None = None,
    var_file: Path | None = None,
) -> DeploymentProvider:
    """Build the `DeploymentProvider` for `context.provider`."""
    if context.provider == Provider.LOCAL:
        from olf.deployment.local.config import LocalDeploymentConfig
        from olf.deployment.local.provider import LocalProvider

        config = LocalDeploymentConfig.from_environment(environ or {}, context=context, var_file=var_file)
        return LocalProvider.create(config, toolkit=toolkit, environ=environ)

    if context.provider in (Provider.AWS, Provider.AZURE):
        from olf.deployment.cloud.aws import AwsBackend
        from olf.deployment.cloud.azure import AzureBackend
        from olf.deployment.cloud.config import CloudDeploymentConfig
        from olf.deployment.cloud.provider import CloudProvider

        config = CloudDeploymentConfig.from_environment(environ or {}, context=context, var_file=var_file)
        backend = AwsBackend() if context.provider == Provider.AWS else AzureBackend()
        return CloudProvider.create(config, backend, toolkit=toolkit, environ=environ)

    raise UnsupportedProviderError(f"provider {context.provider!r} is not supported")
