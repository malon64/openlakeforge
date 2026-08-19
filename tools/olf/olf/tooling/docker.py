"""Thin Docker adapter.

Preserves the "resolve the selected engine endpoint before scoping
DOCKER_CONFIG" behavior from `scripts/lib/common.sh::configure_deployment_scope`
so an isolated DOCKER_CONFIG cannot silently fall back to
`/var/run/docker.sock` on hosts using Colima or another non-default context.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from olf.deployment.errors import CommandExecutionError
from olf.deployment.retry import RetryPolicy, RetryPredicate
from olf.tooling.process import CommandResult, ProcessRunner
from olf.tooling.resolver import ExecutableResolver


class Docker:
    def __init__(self, runner: ProcessRunner, resolver: ExecutableResolver) -> None:
        self._runner = runner
        self._resolver = resolver

    def _executable(self) -> Path:
        return self._resolver.resolve("docker")

    def _run(
        self,
        args: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        check: bool = True,
        input_text: str | None = None,
        retry_policy: RetryPolicy | None = None,
        retry_if: RetryPredicate | None = None,
    ) -> CommandResult:
        argv = [str(self._executable()), *args]
        return self._runner.run(
            argv,
            env=env,
            check=check,
            input_text=input_text,
            retry_policy=retry_policy,
            retry_if=retry_if,
        )

    def context_show(self, *, env: Mapping[str, str] | None = None) -> str:
        return self._run(["context", "show"], env=env).stdout.strip()

    def context_inspect(
        self,
        name: str,
        *,
        format_template: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> str:
        args = ["context", "inspect", name]
        if format_template is not None:
            args += ["--format", format_template]
        return self._run(args, env=env).stdout.strip()

    def resolve_current_engine_endpoint(self, *, env: Mapping[str, str] | None = None) -> str | None:
        """Resolve `.Endpoints.docker.Host` for the currently selected context.

        Returns None (rather than raising) when Docker isn't usable, mirroring
        the shell helper's `|| true` fallback — callers keep any explicit
        DOCKER_HOST override in that case.
        """
        try:
            current = self.context_show(env=env)
            endpoint = self.context_inspect(current, format_template="{{.Endpoints.docker.Host}}", env=env)
        except CommandExecutionError:
            return None
        return endpoint or None

    def build(
        self,
        context_dir: Path,
        *,
        tag: str | None = None,
        file: Path | None = None,
        build_args: Mapping[str, str] | None = None,
        extra_args: Sequence[str] = (),
        env: Mapping[str, str] | None = None,
        retry_policy: RetryPolicy | None = None,
        retry_if: RetryPredicate | None = None,
    ) -> CommandResult:
        args = ["build", str(context_dir)]
        if tag is not None:
            args += ["-t", tag]
        if file is not None:
            args += ["-f", str(file)]
        for key, value in (build_args or {}).items():
            args += ["--build-arg", f"{key}={value}"]
        args.extend(extra_args)
        return self._run(args, env=env, retry_policy=retry_policy, retry_if=retry_if)

    def pull(
        self,
        image: str,
        *,
        env: Mapping[str, str] | None = None,
        retry_policy: RetryPolicy | None = None,
        retry_if: RetryPredicate | None = None,
    ) -> CommandResult:
        return self._run(["pull", image], env=env, retry_policy=retry_policy, retry_if=retry_if)

    def push(
        self,
        image: str,
        *,
        env: Mapping[str, str] | None = None,
        retry_policy: RetryPolicy | None = None,
        retry_if: RetryPredicate | None = None,
    ) -> CommandResult:
        return self._run(["push", image], env=env, retry_policy=retry_policy, retry_if=retry_if)

    def load(self, *, input_path: Path | None = None, env: Mapping[str, str] | None = None) -> CommandResult:
        args = ["load"]
        if input_path is not None:
            args += ["-i", str(input_path)]
        return self._run(args, env=env)

    def tag(self, source: str, target: str, *, env: Mapping[str, str] | None = None) -> CommandResult:
        return self._run(["tag", source, target], env=env)

    def login(
        self,
        registry: str | None = None,
        *,
        username: str | None = None,
        password: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        args = ["login"]
        if username is not None:
            args += ["--username", username]
        if password is not None:
            args.append("--password-stdin")
        if registry is not None:
            args.append(registry)
        return self._run(args, env=env, input_text=password)
