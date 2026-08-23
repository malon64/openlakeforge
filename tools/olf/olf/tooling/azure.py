"""Thin Azure CLI adapter: no Azure SDK resource provisioning, no lifecycle sequencing."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from olf.tooling.process import CommandResult, ProcessRunner
from olf.tooling.resolver import ExecutableResolver


class AzureCli:
    def __init__(self, runner: ProcessRunner, resolver: ExecutableResolver) -> None:
        self._runner = runner
        self._resolver = resolver

    def _executable(self) -> Path:
        return self._resolver.resolve("az")

    def _run(self, args: list[str], *, env: Mapping[str, str] | None = None, check: bool = True) -> CommandResult:
        return self._runner.run([str(self._executable()), *args], env=env, check=check)

    def account_show(self, *, env: Mapping[str, str] | None = None) -> Any:
        result = self._run(["account", "show", "--output", "json"], env=env)
        return json.loads(result.stdout)

    def account_set(self, subscription: str, *, env: Mapping[str, str] | None = None) -> CommandResult:
        return self._run(["account", "set", "--subscription", subscription], env=env)

    def aks_get_credentials(
        self,
        cluster_name: str,
        *,
        resource_group: str,
        kubeconfig_path: Path,
        overwrite: bool = True,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        args = [
            "aks",
            "get-credentials",
            "--resource-group",
            resource_group,
            "--name",
            cluster_name,
            "--file",
            str(kubeconfig_path),
        ]
        if overwrite:
            args.append("--overwrite-existing")
        return self._run(args, env=env)

    def acr_login(self, registry_name: str, *, env: Mapping[str, str] | None = None) -> CommandResult:
        return self._run(["acr", "login", "--name", registry_name], env=env)

    def aks_show(
        self,
        cluster_name: str,
        *,
        resource_group: str,
        env: Mapping[str, str] | None = None,
        check: bool = False,
    ) -> CommandResult:
        return self._run(
            ["aks", "show", "--resource-group", resource_group, "--name", cluster_name],
            env=env,
            check=check,
        )
