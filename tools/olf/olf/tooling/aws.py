"""Thin AWS CLI adapter: no boto3 resource provisioning, no lifecycle sequencing."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from olf.tooling.process import CommandResult, ProcessRunner
from olf.tooling.resolver import ExecutableResolver


class AwsCli:
    def __init__(self, runner: ProcessRunner, resolver: ExecutableResolver) -> None:
        self._runner = runner
        self._resolver = resolver

    def _executable(self) -> Path:
        return self._resolver.resolve("aws")

    def _run(self, args: list[str], *, env: Mapping[str, str] | None = None, check: bool = True) -> CommandResult:
        return self._runner.run([str(self._executable()), *args], env=env, check=check)

    def sts_get_caller_identity(self, *, env: Mapping[str, str] | None = None) -> Any:
        result = self._run(["sts", "get-caller-identity", "--output", "json"], env=env)
        return json.loads(result.stdout)

    def eks_update_kubeconfig(
        self,
        cluster_name: str,
        *,
        region: str,
        kubeconfig_path: Path,
        alias: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        args = [
            "eks",
            "update-kubeconfig",
            "--region",
            region,
            "--name",
            cluster_name,
            "--kubeconfig",
            str(kubeconfig_path),
        ]
        if alias is not None:
            args += ["--alias", alias]
        return self._run(args, env=env)

    def ecr_get_login_password(self, *, region: str, env: Mapping[str, str] | None = None) -> str:
        result = self._run(["ecr", "get-login-password", "--region", region], env=env)
        return result.stdout.strip()

    def eks_describe_cluster(
        self,
        cluster_name: str,
        *,
        region: str,
        env: Mapping[str, str] | None = None,
        check: bool = False,
    ) -> CommandResult:
        return self._run(
            ["eks", "describe-cluster", "--region", region, "--name", cluster_name],
            env=env,
            check=check,
        )
