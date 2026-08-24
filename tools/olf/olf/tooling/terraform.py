"""Thin Terraform adapter: structured argv only, no HCL/state logic in Python."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from olf.deployment.errors import CommandExecutionError
from olf.deployment.retry import RetryPolicy, RetryPredicate
from olf.tooling.process import CommandResult, ProcessRunner
from olf.tooling.resolver import ExecutableResolver


class Terraform:
    def __init__(self, runner: ProcessRunner, resolver: ExecutableResolver) -> None:
        self._runner = runner
        self._resolver = resolver

    def _executable(self) -> Path:
        return self._resolver.resolve("terraform")

    def _run(
        self,
        terraform_dir: Path,
        args: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        check: bool = True,
        retry_policy: RetryPolicy | None = None,
        retry_if: RetryPredicate | None = None,
        stream_output: bool = False,
    ) -> CommandResult:
        argv = [str(self._executable()), f"-chdir={terraform_dir}", *args]
        return self._runner.run(
            argv,
            env=env,
            check=check,
            retry_policy=retry_policy,
            retry_if=retry_if,
            stream_output=stream_output,
        )

    @staticmethod
    def _var_args(var_files: Sequence[Path | str], variables: Mapping[str, str] | None) -> list[str]:
        args: list[str] = [f"-var-file={var_file}" for var_file in var_files]
        for key, value in (variables or {}).items():
            args.append(f"-var={key}={value}")
        return args

    def init(
        self,
        terraform_dir: Path,
        *,
        extra_args: Sequence[str] = (),
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        return self._run(terraform_dir, ["init", *extra_args], env=env, stream_output=True)

    def apply(
        self,
        terraform_dir: Path,
        *,
        auto_approve: bool = True,
        var_files: Sequence[Path | str] = (),
        variables: Mapping[str, str] | None = None,
        extra_args: Sequence[str] = (),
        env: Mapping[str, str] | None = None,
        retry_policy: RetryPolicy | None = None,
        retry_if: RetryPredicate | None = None,
    ) -> CommandResult:
        args = ["apply"]
        if auto_approve:
            args.append("-auto-approve")
        args.extend(self._var_args(var_files, variables))
        args.extend(extra_args)
        return self._run(
            terraform_dir, args, env=env, retry_policy=retry_policy, retry_if=retry_if, stream_output=True
        )

    def destroy(
        self,
        terraform_dir: Path,
        *,
        auto_approve: bool = True,
        var_files: Sequence[Path | str] = (),
        variables: Mapping[str, str] | None = None,
        extra_args: Sequence[str] = (),
        env: Mapping[str, str] | None = None,
        retry_policy: RetryPolicy | None = None,
        retry_if: RetryPredicate | None = None,
    ) -> CommandResult:
        args = ["destroy"]
        if auto_approve:
            args.append("-auto-approve")
        args.extend(self._var_args(var_files, variables))
        args.extend(extra_args)
        return self._run(
            terraform_dir, args, env=env, retry_policy=retry_policy, retry_if=retry_if, stream_output=True
        )

    def output_raw(self, terraform_dir: Path, name: str, *, env: Mapping[str, str] | None = None) -> str:
        result = self._run(terraform_dir, ["output", "-raw", name], env=env)
        return result.stdout.strip()

    def output_json(self, terraform_dir: Path, name: str, *, env: Mapping[str, str] | None = None) -> Any:
        result = self._run(terraform_dir, ["output", "-json", name], env=env)
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise CommandExecutionError(
                result.argv,
                result.returncode,
                stdout=result.stdout,
                stderr=f"terraform output {name!r} did not return valid JSON",
            ) from exc

    def state_show(
        self,
        terraform_dir: Path,
        resource_addr: str,
        *,
        env: Mapping[str, str] | None = None,
        check: bool = False,
    ) -> CommandResult:
        return self._run(terraform_dir, ["state", "show", resource_addr], env=env, check=check)

    def import_resource(
        self,
        terraform_dir: Path,
        resource_addr: str,
        resource_id: str,
        *,
        var_files: Sequence[Path | str] = (),
        variables: Mapping[str, str] | None = None,
        extra_args: Sequence[str] = (),
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        args = ["import", *self._var_args(var_files, variables), *extra_args, resource_addr, resource_id]
        return self._run(terraform_dir, args, env=env)
