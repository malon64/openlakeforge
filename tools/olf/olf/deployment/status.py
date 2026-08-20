"""Provider-neutral cluster status reporting.

Port of the `local-status` Make target (`kubectl get pods/svc/pvc`); kept
provider-neutral so #125 can reuse it verbatim for AWS/Azure.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from olf.deployment.errors import DeploymentPreconditionError
from olf.tooling.kubectl import Kubectl

DEFAULT_RESOURCES: tuple[tuple[str, str], ...] = (
    ("Pods", "pods"),
    ("Services", "svc"),
    ("PVCs", "pvc"),
)


@dataclass(frozen=True)
class StatusSection:
    title: str
    output: str


@dataclass(frozen=True)
class StatusReport:
    sections: tuple[StatusSection, ...]

    def render(self) -> str:
        blocks = [f"=== {section.title} ===\n{section.output}" for section in self.sections]
        return "\n\n".join(blocks)


def collect_status(
    kubectl: Kubectl,
    *,
    namespace: str,
    context: str,
    kubeconfig: Path,
    env: Mapping[str, str] | None = None,
    resources: Sequence[tuple[str, str]] = DEFAULT_RESOURCES,
) -> StatusReport:
    sections = []
    for title, resource in resources:
        result = kubectl.get(
            resource,
            namespace=namespace,
            context=context,
            kubeconfig=kubeconfig,
            env=env,
            check=False,
        )
        if not result.ok:
            detail = result.stderr.strip() or f"kubectl exited {result.returncode}"
            raise DeploymentPreconditionError(f"failed to query {title} in namespace '{namespace}': {detail}")
        output = result.stdout.strip() or result.stderr.strip()
        sections.append(StatusSection(title=title, output=output))
    return StatusReport(sections=tuple(sections))
