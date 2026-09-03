"""Thin Helm adapter: low-level chart/repo primitives only.

Higher-level chart-cache policy (e.g. re-packing the Dagster chart without
its values schema) belongs to a future deployment-lifecycle issue, not here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from olf.deployment.retry import RetryPolicy, RetryPredicate
from olf.tooling.process import CommandResult, ProcessRunner
from olf.tooling.resolver import ExecutableResolver


class Helm:
    def __init__(self, runner: ProcessRunner, resolver: ExecutableResolver) -> None:
        self._runner = runner
        self._resolver = resolver

    def _executable(self) -> Path:
        return self._resolver.resolve("helm")

    def _run(
        self,
        args: Sequence[str],
        *,
        kube_context: str | None = None,
        repository_config: Path | None = None,
        repository_cache: Path | None = None,
        env: Mapping[str, str] | None = None,
        check: bool = True,
        retry_policy: RetryPolicy | None = None,
        retry_if: RetryPredicate | None = None,
    ) -> CommandResult:
        argv = [str(self._executable())]
        if kube_context is not None:
            argv += ["--kube-context", kube_context]
        argv.extend(args)
        merged_env: dict[str, str] = dict(env or {})
        if repository_config is not None:
            merged_env["HELM_REPOSITORY_CONFIG"] = str(repository_config)
        if repository_cache is not None:
            merged_env["HELM_REPOSITORY_CACHE"] = str(repository_cache)
        return self._runner.run(
            argv,
            env=merged_env or None,
            check=check,
            retry_policy=retry_policy,
            retry_if=retry_if,
        )

    def status(
        self,
        release: str,
        *,
        namespace: str,
        kube_context: str | None = None,
        env: Mapping[str, str] | None = None,
        check: bool = False,
    ) -> CommandResult:
        return self._run(
            ["status", release, "-n", namespace],
            kube_context=kube_context,
            env=env,
            check=check,
        )

    def uninstall(
        self,
        release: str,
        *,
        namespace: str,
        kube_context: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        return self._run(
            ["uninstall", release, "-n", namespace],
            kube_context=kube_context,
            env=env,
        )

    def upgrade_install(
        self,
        release: str,
        chart: Path,
        *,
        namespace: str,
        values: Path,
        kube_context: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        """Install a release atomically, or atomically reconcile an existing one."""
        return self._run(
            [
                "upgrade",
                "--install",
                release,
                str(chart),
                "--namespace",
                namespace,
                "--values",
                str(values),
                "--atomic",
                "--wait",
            ],
            kube_context=kube_context,
            env=env,
        )

    def repo_add(
        self,
        name: str,
        url: str,
        *,
        force_update: bool = True,
        repository_config: Path | None = None,
        repository_cache: Path | None = None,
        env: Mapping[str, str] | None = None,
        retry_policy: RetryPolicy | None = None,
        retry_if: RetryPredicate | None = None,
    ) -> CommandResult:
        args = ["repo", "add", name, url]
        if force_update:
            args.append("--force-update")
        return self._run(
            args,
            repository_config=repository_config,
            repository_cache=repository_cache,
            env=env,
            retry_policy=retry_policy,
            retry_if=retry_if,
        )

    def repo_update(
        self,
        *,
        repository_config: Path | None = None,
        repository_cache: Path | None = None,
        env: Mapping[str, str] | None = None,
        retry_policy: RetryPolicy | None = None,
        retry_if: RetryPredicate | None = None,
    ) -> CommandResult:
        return self._run(
            ["repo", "update"],
            repository_config=repository_config,
            repository_cache=repository_cache,
            env=env,
            retry_policy=retry_policy,
            retry_if=retry_if,
        )

    def pull(
        self,
        chart_ref: str,
        *,
        version: str | None = None,
        destination: Path | None = None,
        untar: bool = False,
        untar_dir: Path | None = None,
        repository_config: Path | None = None,
        repository_cache: Path | None = None,
        env: Mapping[str, str] | None = None,
        retry_policy: RetryPolicy | None = None,
        retry_if: RetryPredicate | None = None,
    ) -> CommandResult:
        args = ["pull", chart_ref]
        if version is not None:
            args += ["--version", version]
        if destination is not None:
            args += ["--destination", str(destination)]
        if untar:
            args.append("--untar")
        if untar_dir is not None:
            args += ["--untardir", str(untar_dir)]
        return self._run(
            args,
            repository_config=repository_config,
            repository_cache=repository_cache,
            env=env,
            retry_policy=retry_policy,
            retry_if=retry_if,
        )

    def show_chart(
        self,
        chart_path: Path,
        *,
        repository_config: Path | None = None,
        repository_cache: Path | None = None,
        env: Mapping[str, str] | None = None,
        check: bool = False,
    ) -> CommandResult:
        return self._run(
            ["show", "chart", str(chart_path)],
            repository_config=repository_config,
            repository_cache=repository_cache,
            env=env,
            check=check,
        )

    def package(
        self,
        chart_dir: Path,
        *,
        destination: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        args = ["package", str(chart_dir)]
        if destination is not None:
            args += ["--destination", str(destination)]
        return self._run(args, env=env)
