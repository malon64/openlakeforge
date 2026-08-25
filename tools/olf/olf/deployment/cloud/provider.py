"""`DeploymentProvider` implementation for the AWS/Azure cloud stack.

Mirrors `olf.deployment.local.provider.LocalProvider`: every method is a
thin delegate into a sibling `olf.deployment.cloud.*` module, so the
granular Make target delegates and the combined `olf deploy`/`olf destroy`
verbs share the exact same code path.

The one wrinkle local does not have: `DeploymentContext.kube_context` is
unknown until the foundation's Terraform outputs are read (local's is the
static `kind-<cluster>`), so `_foundation_facts` MUST resolve before `env`
is built - `env` bakes `KUBE_CONTEXT` into the command environment via
`DeploymentContext.command_env`, and a context built before `foundation_up`
has run has no `kube_context` to bake in yet. `_base_env` (no kube_context)
is what `foundation_up`/`foundation_down` use instead; every phase after
foundation reads `env`, which resolves `_foundation_facts` first.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from functools import cached_property
from typing import TYPE_CHECKING

from olf.deployment.cloud.backend import CloudBackend, FoundationFacts
from olf.deployment.cloud.config import CloudDeploymentConfig
from olf.deployment.engine import DeploymentPhase, Toolkit
from olf.deployment.inspection import DoctorItem, DoctorReport, base_report, docker_health

if TYPE_CHECKING:
    from olf.deployment.context import DeploymentContext
    from olf.deployment.status import StatusReport


@dataclass
class CloudProvider:
    config: CloudDeploymentConfig
    backend: CloudBackend
    tools: Toolkit
    _environ: Mapping[str, str] = field(default_factory=lambda: os.environ)

    @classmethod
    def create(
        cls,
        config: CloudDeploymentConfig,
        backend: CloudBackend,
        *,
        toolkit: Toolkit | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> CloudProvider:
        return cls(
            config=config, backend=backend, tools=toolkit or Toolkit.default(), _environ=environ or os.environ
        )

    @property
    def context(self) -> DeploymentContext:
        return self.config.context

    @cached_property
    def _base_env(self) -> dict[str, str]:
        """Command environment without a resolved `KUBE_CONTEXT` - see module docstring."""
        docker_host = None
        if not self._environ.get("DOCKER_HOST"):
            docker_host = self.tools.docker.resolve_current_engine_endpoint(env=dict(self._environ))
        self.config.context.prepare_directories()
        return self.config.context.command_env(docker_host=docker_host)

    @cached_property
    def _foundation_facts(self) -> FoundationFacts:
        from olf.deployment.cloud import foundation

        return foundation.require_foundation_facts(self.config, self.tools, self.backend, env=self._base_env)

    @cached_property
    def env(self) -> dict[str, str]:
        """The command environment, with `KUBE_CONTEXT` resolved from the foundation.

        Must not be read before the foundation phase has run.
        """
        facts = self._foundation_facts
        resolved_context = replace(self.context, kube_context=facts.kube_context)
        return resolved_context.command_env(base=self._base_env, docker_host=self._base_env.get("DOCKER_HOST"))

    def foundation_up(self) -> None:
        from olf.deployment.cloud import foundation

        foundation.foundation_up(self.config, self.tools, self.backend, environ=self._environ, env=self._base_env)
        self.__dict__.pop("_foundation_facts", None)
        self.__dict__.pop("env", None)

    def foundation_down(self, *, force: bool = False) -> None:
        from olf.deployment.cloud import foundation

        foundation.foundation_down(
            self.config,
            self.tools,
            self.backend,
            environ=self._environ,
            env=self._base_env,
            force=force or self.config.force_foundation_down,
        )

    def prepare_images(self) -> None:
        """No-op: cloud has no kind-prefetch equivalent. `--phase prefetch` is intentionally inert."""

    def platform_up(self) -> None:
        from olf.deployment.cloud import platform

        platform.platform_up(self.config, self.tools, self.backend, self._foundation_facts, env=self.env)

    def platform_down(self) -> None:
        from olf.deployment.cloud import teardown

        teardown.platform_down(self.config, self.tools, self.backend, self._foundation_facts, env=self.env)

    def artifacts_deploy(self) -> None:
        from olf.deployment.cloud import artifacts

        artifacts.artifacts_deploy(self.config, self.tools, self.backend, self._foundation_facts, env=self.env)

    def status(self) -> StatusReport:
        from olf.deployment.status import collect_status

        facts = self._foundation_facts
        return collect_status(
            self.tools.kubectl,
            namespace=self.config.namespace,
            context=facts.kube_context,
            kubeconfig=self.config.paths.kubeconfig_path,
            env=self.env,
        )

    def forward(self) -> None:
        from olf.deployment.cloud import forward as forward_module
        from olf.deployment.portforward import PortForwardSupervisor

        facts = self._foundation_facts
        spec = forward_module.cloud_forward_spec(self.config, self.backend, kube_context=facts.kube_context)
        for line in spec.banner:
            print(line)  # noqa: T201 - user-facing CLI banner
        supervisor = PortForwardSupervisor(self.tools.kubectl, log_prefix=self.config.paths.port_forward_log_prefix)
        supervisor.run(spec, env=self.env)

    def plan(self, phase: DeploymentPhase) -> bool:
        from olf.deployment.cloud import foundation, platform
        from olf.deployment.errors import DeploymentPreconditionError

        changes = False
        if phase in (DeploymentPhase.ALL, DeploymentPhase.FOUNDATION):
            foundation_dir = self.config.paths.foundation_terraform_dir
            tfvars = foundation._resolve_foundation_tfvars_file(
                self.config, self.backend, self._environ, foundation_dir
            )
            self.tools.terraform.init(foundation_dir, env=self._base_env)
            result = self.tools.terraform.plan(
                foundation_dir,
                var_files=(str(tfvars),) if tfvars is not None else (),
                variables=self.backend.foundation_apply_variables(self.config, self._environ),
                detailed_exitcode=True,
                env=self._base_env,
            )
            changes = changes or result.returncode == 2
        if phase in (DeploymentPhase.ALL, DeploymentPhase.PLATFORM):
            if not self.config.paths.foundation_state_path.is_file():
                if phase == DeploymentPhase.PLATFORM:
                    raise DeploymentPreconditionError("foundation state is required before planning the cloud platform")
                return changes
            facts = self._foundation_facts
            platform_dir = self.config.paths.platform_terraform_dir
            platform.prepare_charts(self.config, self.tools, env=self.env)
            self.tools.terraform.init(platform_dir, env=self.env)
            result = self.tools.terraform.plan(
                platform_dir,
                var_files=(str(self.config.terraform.var_file),) if self.config.terraform.var_file is not None else (),
                variables=self.backend.platform_apply_variables(self.config, facts),
                detailed_exitcode=True,
                env=self.env,
            )
            changes = changes or result.returncode == 2
        return changes

    def doctor(self, phase: DeploymentPhase) -> DoctorReport:
        from olf.deployment.cloud import foundation
        from olf.deployment.errors import DeploymentError

        provider_cli = "aws" if self.backend.scope == "aws" else "az"
        required = ["terraform", "kubectl", provider_cli]
        if phase in (DeploymentPhase.ALL, DeploymentPhase.PLATFORM):
            required.append("helm")
        needs_docker = phase in (DeploymentPhase.ALL, DeploymentPhase.ARTIFACTS) or (
            phase == DeploymentPhase.PLATFORM and self.config.features.analytics_enabled
        )
        if needs_docker:
            required.append("docker")
        items = base_report(repo_root=self.config.paths.repo_root, tools=self.tools, required_tools=required)
        env = self.context.command_env()
        if needs_docker:
            items.append(docker_health(self.tools, env=env))
        try:
            self.backend.preflight(self.tools, env=env)
        except Exception as exc:  # provider adapters provide the actionable message
            items.append(DoctorItem(f"{self.backend.scope} authentication", False, str(exc)))
        else:
            items.append(DoctorItem(f"{self.backend.scope} authentication", True, "authenticated"))
        if phase in (DeploymentPhase.ALL, DeploymentPhase.FOUNDATION):
            foundation_dir = self.config.paths.foundation_terraform_dir
            try:
                tfvars = foundation._resolve_foundation_tfvars_file(  # noqa: SLF001 - shared foundation resolver.
                    self.config, self.backend, self._environ, foundation_dir
                )
            except DeploymentError as exc:
                items.append(DoctorItem(f"{self.backend.scope} foundation tfvars", False, str(exc)))
            else:
                if tfvars is not None:
                    items.append(
                        DoctorItem(f"{self.backend.scope} foundation tfvars", tfvars.is_file(), str(tfvars))
                    )
        if phase in (DeploymentPhase.ALL, DeploymentPhase.PLATFORM):
            tfvars = self.config.terraform.var_file
            if tfvars is not None:
                items.append(DoctorItem(f"{self.backend.scope} platform tfvars", tfvars.is_file(), str(tfvars)))
        if phase in (DeploymentPhase.PLATFORM, DeploymentPhase.ARTIFACTS):
            items.append(
                DoctorItem(
                    "foundation state",
                    self.config.paths.foundation_state_path.is_file(),
                    str(self.config.paths.foundation_state_path),
                )
            )
        return DoctorReport(tuple(items))
