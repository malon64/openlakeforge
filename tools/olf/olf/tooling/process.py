"""The one process-execution API for new deployment code.

Every command is executed as structured argv (`shell=False`); nothing here
ever builds or interprets a shell command string.
"""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from olf.deployment.errors import CommandExecutionError, CommandTimeoutError, ExecutableNotFoundError
from olf.deployment.retry import RetryPolicy, RetryPredicate, run_with_retry
from olf.tooling.redact import redact_argv


@dataclass(frozen=True)
class Command:
    argv: tuple[str, ...]
    cwd: Path | None = None
    env: Mapping[str, str] | None = None
    timeout_seconds: float | None = None
    input_text: str | None = None

    def __post_init__(self) -> None:
        argv = tuple(str(part) for part in self.argv)
        if not argv:
            raise ValueError("Command.argv must not be empty")
        object.__setattr__(self, "argv", argv)


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _to_command(
    command: Command | Sequence[str | Path],
    *,
    cwd: Path | None,
    env: Mapping[str, str] | None,
    timeout_seconds: float | None,
    input_text: str | None,
) -> Command:
    if isinstance(command, Command):
        return command
    return Command(
        argv=tuple(str(part) for part in command),
        cwd=cwd,
        env=env,
        timeout_seconds=timeout_seconds,
        input_text=input_text,
    )


class ProcessRunner:
    """Executes `Command`s with `subprocess.run`, no shell involvement.

    `env`, when given, is layered on top of a copy of the current process
    environment (never `os.environ` itself) so callers only need to specify
    the deployment-scoped overrides they care about, while the parent
    process's environment is left untouched.
    """

    def __init__(self, *, log_commands: bool = False) -> None:
        self._log_commands = log_commands

    def run(
        self,
        command: Command | Sequence[str | Path],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout_seconds: float | None = None,
        input_text: str | None = None,
        check: bool = True,
        retry_policy: RetryPolicy | None = None,
        retry_if: RetryPredicate | None = None,
    ) -> CommandResult:
        cmd = _to_command(
            command,
            cwd=cwd,
            env=env,
            timeout_seconds=timeout_seconds,
            input_text=input_text,
        )

        def attempt() -> CommandResult:
            return self._run_once(cmd, check=check)

        if retry_policy is not None:
            return run_with_retry(attempt, policy=retry_policy, retry_if=retry_if)
        return attempt()

    def _run_once(self, command: Command, *, check: bool) -> CommandResult:
        argv = list(command.argv)
        full_env = {**os.environ, **command.env} if command.env is not None else None

        if self._log_commands:
            from olf import log

            sanitized = " ".join(redact_argv(argv))
            log.step(f"run: {sanitized}")

        started = time.perf_counter()
        try:
            completed = subprocess.run(  # noqa: S603 - argv is structured, shell=False
                argv,
                cwd=command.cwd,
                env=full_env,
                capture_output=True,
                text=True,
                timeout=command.timeout_seconds,
                input=command.input_text,
                stdin=None if command.input_text is not None else subprocess.DEVNULL,
                check=False,
                shell=False,
            )
        except FileNotFoundError as exc:
            raise ExecutableNotFoundError(argv[0]) from exc
        except subprocess.TimeoutExpired as exc:
            raise CommandTimeoutError(tuple(argv), command.timeout_seconds or 0.0) from exc

        duration = time.perf_counter() - started
        result = CommandResult(
            argv=tuple(argv),
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            duration_seconds=duration,
        )

        if check and result.returncode != 0:
            raise CommandExecutionError(
                result.argv,
                result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                cwd=command.cwd,
                env=command.env,
            )
        return result
