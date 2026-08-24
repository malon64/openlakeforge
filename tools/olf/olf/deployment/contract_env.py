"""Provider-neutral contract-environment activation.

Replaces sourcing `scripts/contracts/load-runtime-env.sh`: calls
`olf.contracts.load_provider_contracts`/`build_contract_env` directly and
applies the exports/unsets to `os.environ` for the duration of a block,
restoring the previous values on exit. This is the in-process bridge to the
existing `os.environ`-reading library modules (`olf.k8s`, `olf.config`,
`olf.s3`, `olf.layers`) that every provider's artifact deployment needs, so
it lives at `olf.deployment` rather than under `olf.deployment.local` -
issue #125's AWS/Azure providers reuse it unmodified.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from olf import contracts


@contextmanager
def applied_contract_environment(
    *,
    contract_terraform_dir: Path,
    repo_root: Path,
    namespace: str,
    kube_context: str,
    kubeconfig_path: Path,
    port_forward_log_prefix: Path,
) -> Iterator[dict[str, str]]:
    provider_contracts = contracts.load_provider_contracts(str(contract_terraform_dir))
    exports, unsets = contracts.build_contract_env(os.environ, provider_contracts, repo_root=repo_root)

    extra = {
        "OPENLAKEFORGE_REPO_ROOT": str(repo_root),
        "NAMESPACE": namespace,
        "KUBE_CONTEXT": kube_context,
        "KUBECONFIG": str(kubeconfig_path),
        "OPENLAKEFORGE_PORT_FORWARD_LOG_PREFIX": str(port_forward_log_prefix),
    }

    previous: dict[str, str | None] = {}
    applied = {**exports, **extra}
    for name, value in applied.items():
        previous[name] = os.environ.get(name)
        os.environ[name] = value
    for name in unsets:
        previous.setdefault(name, os.environ.get(name))
        os.environ.pop(name, None)

    try:
        yield dict(os.environ)
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
