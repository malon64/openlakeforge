"""`DeploymentProvider` implementation for the local (kind-based) stack.

Every method is a one-line delegate into a sibling `olf.deployment.local.*`
module, so the granular Make target delegates (`--phase foundation`, etc.)
and the combined `olf deploy`/`olf destroy` verbs share the exact same code
path - nothing is duplicated between them.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING

from olf.deployment.engine import DeploymentPhase, Toolkit
from olf.deployment.inspection import DoctorItem, DoctorReport, base_report, docker_health
from olf.deployment.local.config import LocalDeploymentConfig

if TYPE_CHECKING:
    from olf.deployment.context import DeploymentContext
    from olf.deployment.status import StatusReport


@dataclass
class LocalProvider:
    config: LocalDeploymentConfig
    tools: Toolkit
    _environ: Mapping[str, str] = field(default_factory=lambda: os.environ)

    @classmethod
    def create(
        cls,
        config: LocalDeploymentConfig,
        *,
        toolkit: Toolkit | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> LocalProvider:
        return cls(config=config, tools=toolkit or Toolkit.default(), _environ=environ or os.environ)

    @property
    def context(self) -> DeploymentContext:
        return self.config.context

    @cached_property
    def env(self) -> dict[str, str]:
        """The command-scoped environment overlay, built once per provider instance.

        Reproduces `scripts/lib/common.sh::configure_deployment_scope`'s
        ordering exactly: resolve the ambient Docker context's real engine
        endpoint (only when `DOCKER_HOST` isn't already set) before scoping
        `DOCKER_CONFIG`, so an isolated config can't silently fall back to
        `/var/run/docker.sock`.

        Deliberately built without `self._environ` as `base`: this dict is
        what `ProcessRunner` stores verbatim on `CommandExecutionError` for
        diagnostics, and `ProcessRunner._run_once` already layers it over a
        full `os.environ` snapshot for actual execution - so carrying the
        ambient environment forward here too would only leak arbitrary
        inherited secrets (e.g. `DATABASE_URL`) into failure output without
        changing what a subprocess actually sees.
        """
        docker_host = None
        if not self._environ.get("DOCKER_HOST"):
            docker_host = self.tools.docker.resolve_current_engine_endpoint(env=dict(self._environ))
        self.context.prepare_directories()
        return self.context.command_env(docker_host=docker_host)

    def foundation_up(self) -> None:
        from olf.deployment.local import foundation

        foundation.foundation_up(self.config, self.tools, env=self.env)

    def foundation_down(self, *, force: bool = False) -> None:
        from olf.deployment.local import foundation

        foundation.foundation_down(
            self.config, self.tools, env=self.env, force=force or self.config.force_foundation_down
        )

    def prepare_images(self) -> None:
        from olf.deployment.local import prefetch

        prefetch.prefetch_images(self.config, self.tools, env=self.env)

    def platform_up(self) -> None:
        from olf.deployment.local import platform

        platform.platform_up(self.config, self.tools, env=self.env)

    def platform_down(self) -> None:
        from olf.deployment.local import teardown

        teardown.platform_down(self.config, self.tools, env=self.env)

    def artifacts_deploy(self) -> None:
        from olf.deployment.local import artifacts

        artifacts.artifacts_deploy(self.config, self.tools, env=self.env)

    def status(self) -> StatusReport:
        from olf.deployment.status import collect_status

        return collect_status(
            self.tools.kubectl,
            namespace=self.config.namespace,
            context=self.config.kube_context,
            kubeconfig=self.config.paths.kubeconfig_path,
            env=self.env,
        )

    def forward(self) -> None:
        from olf.deployment.local import forward as forward_module
        from olf.deployment.portforward import PortForwardSupervisor

        spec = forward_module.local_forward_spec(self.config, self.tools, env=self.env)
        for line in spec.banner:
            print(line)  # noqa: T201 - user-facing CLI banner
        supervisor = PortForwardSupervisor(self.tools.kubectl, log_prefix=self.config.paths.port_forward_log_prefix)
        supervisor.run(spec, env=self.env)

    def plan(self, phase: DeploymentPhase) -> bool:
        from olf.deployment.errors import DeploymentPreconditionError
        from olf.deployment.local import foundation, platform

        changes = False
        if phase in (DeploymentPhase.ALL, DeploymentPhase.FOUNDATION):
            self.context.prepare_directories()
            self.tools.terraform.init(self.config.paths.foundation_terraform_dir, env=self.env)
            result = self.tools.terraform.plan(
                self.config.paths.foundation_terraform_dir,
                variables=foundation.foundation_apply_variables(self.config, self.tools),
                detailed_exitcode=True,
                env=self.env,
            )
            changes = changes or result.returncode == 2
        if phase in (DeploymentPhase.ALL, DeploymentPhase.PLATFORM):
            if not self.config.paths.foundation_state_path.is_file():
                if phase == DeploymentPhase.PLATFORM:
                    raise DeploymentPreconditionError("foundation state is required before planning the local platform")
                return changes
            platform.prepare_charts(self.config, self.tools, env=self.env)
            self.tools.terraform.init(self.config.paths.platform_terraform_dir, env=self.env)
            result = self.tools.terraform.plan(
                self.config.paths.platform_terraform_dir,
                var_files=platform.platform_var_files(self.config),
                variables=platform.platform_apply_variables(self.config),
                detailed_exitcode=True,
                env=self.env,
            )
            changes = changes or result.returncode == 2
        return changes

    def doctor(self, phase: DeploymentPhase) -> DoctorReport:
        required = ["terraform", "kubectl", "docker", "kind"]
        if phase in (DeploymentPhase.ALL, DeploymentPhase.PLATFORM):
            required.append("helm")
        items = base_report(
            repo_root=self.config.paths.repo_root,
            tools=self.tools,
            required_tools=required,
        )
        items.append(docker_health(self.tools, env=self.context.command_env()))
        if phase in (DeploymentPhase.ALL, DeploymentPhase.PLATFORM):
            tfvars = self.config.terraform.var_file
            if tfvars is not None:
                items.append(DoctorItem("local platform tfvars", tfvars.is_file(), str(tfvars)))
        if phase in (DeploymentPhase.PLATFORM, DeploymentPhase.ARTIFACTS):
            items.append(
                DoctorItem(
                    "foundation state",
                    self.config.paths.foundation_state_path.is_file(),
                    str(self.config.paths.foundation_state_path),
                )
            )
        if phase in (DeploymentPhase.ALL, DeploymentPhase.ARTIFACTS):
            from olf.contracts import ProviderContractError, load_provider_contracts

            contract_dir = Path(
                self._environ.get("OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR", self.config.paths.platform_terraform_dir)
            ).resolve()
            try:
                provider_contracts = load_provider_contracts(str(contract_dir))
            except ProviderContractError as exc:
                items.append(DoctorItem("local platform provider contracts", False, str(exc)))
            else:
                detail = str(contract_dir) if provider_contracts is not None else f"unavailable from {contract_dir}"
                items.append(DoctorItem("local platform provider contracts", provider_contracts is not None, detail))
        return DoctorReport(tuple(items))
