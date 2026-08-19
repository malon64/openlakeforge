"""Provider-neutral multi-target `kubectl port-forward` supervision.

Port of the Makefile's inline `local-forward` target: backgrounds one
`kubectl port-forward` child per service, and guarantees every child is
cleaned up on exit, exception, or `SIGINT`/`SIGTERM` - the Python equivalent
of the shell target's `trap ... INT TERM EXIT; wait`.

`Kubectl.port_forward` is a blocking, single-call adapter method (used for
one-shot waits elsewhere); it is the wrong shape for N concurrent,
long-lived, backgrounded forwards, so this module builds argv through
`Kubectl.port_forward_argv` and owns the `subprocess.Popen` lifecycle itself.
"""

from __future__ import annotations

import signal
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from olf.tooling.kubectl import Kubectl

_TERMINATE_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class ForwardTarget:
    label: str
    resource: str
    local_port: int
    remote_port: int

    @property
    def ports(self) -> str:
        return f"{self.local_port}:{self.remote_port}"


@dataclass(frozen=True)
class ForwardSpec:
    targets: tuple[ForwardTarget, ...]
    namespace: str
    context: str
    kubeconfig: Path
    banner: tuple[str, ...] = ()


class PortForwardSupervisor:
    def __init__(
        self,
        kubectl: Kubectl,
        *,
        log_prefix: Path,
        popen: Callable[..., subprocess.Popen] = subprocess.Popen,
    ) -> None:
        self._kubectl = kubectl
        self._log_prefix = log_prefix
        self._popen = popen
        self._processes: list[subprocess.Popen] = []
        self._log_files: list = []

    def start(
        self, target: ForwardTarget, spec: ForwardSpec, *, env: Mapping[str, str] | None = None
    ) -> subprocess.Popen:
        argv = self._kubectl_argv(target, spec)
        log_path = Path(f"{self._log_prefix}-{target.label}-port-forward.log")
        log_file = open(log_path, "w")  # noqa: SIM115 - lifetime tied to the child process
        process = self._popen(argv, stdout=log_file, stderr=subprocess.STDOUT, env=dict(env) if env else None)
        self._processes.append(process)
        self._log_files.append(log_file)
        return process

    def _kubectl_argv(self, target: ForwardTarget, spec: ForwardSpec) -> list[str]:
        argv = self._kubectl.port_forward_argv(
            target.resource, [target.ports], namespace=spec.namespace, context=spec.context
        )
        return argv

    def stop_all(self) -> None:
        for process in self._processes:
            if process.poll() is not None:
                continue
            process.terminate()
        for process in self._processes:
            try:
                process.wait(timeout=_TERMINATE_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()
        for log_file in self._log_files:
            try:
                log_file.close()
            except OSError:
                pass
        self._processes = []
        self._log_files = []

    def run(
        self,
        spec: ForwardSpec,
        *,
        env: Mapping[str, str] | None = None,
        wait: Callable[[], None] | None = None,
    ) -> None:
        installed: list[tuple[int, object]] = []

        def _handle_signal(signum, frame):  # noqa: ANN001, ARG001
            self.stop_all()
            raise SystemExit(0)

        try:
            for sig in (signal.SIGINT, signal.SIGTERM):
                installed.append((sig, signal.getsignal(sig)))
                signal.signal(sig, _handle_signal)

            for target in spec.targets:
                self.start(target, spec, env=env)

            if wait is not None:
                wait()
            elif self._processes:
                self._processes[0].wait()
        finally:
            self.stop_all()
            for sig, previous in installed:
                signal.signal(sig, previous)
