"""Read-only deployment inspection helpers used by ``olf doctor``.

The deployment engine owns preflight checks rather than leaving them in Make
or shell wrappers.  The report is deliberately small: managed toolchains and
version installation are an extension point for issue #127.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from olf.deployment.errors import DeploymentError, ExecutableNotFoundError

if TYPE_CHECKING:
    from olf.deployment.engine import Toolkit


@dataclass(frozen=True)
class DoctorItem:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class DoctorReport:
    items: tuple[DoctorItem, ...]

    @property
    def ok(self) -> bool:
        return all(item.ok for item in self.items)

    def render(self) -> str:
        lines = ["OpenLakeForge deployment preflight"]
        for item in self.items:
            status = "PASS" if item.ok else "FAIL"
            lines.append(f"{status:4} {item.name}: {item.detail}")
        return "\n".join(lines)


def base_report(
    *,
    repo_root: Path,
    tools: Toolkit,
    required_tools: Iterable[str],
) -> list[DoctorItem]:
    items = [
        DoctorItem("repository", (repo_root / "lakehouse_code/lakehouse.yaml").is_file(), str(repo_root)),
        DoctorItem("terraform roots", (repo_root / "infra/terraform").is_dir(), str(repo_root / "infra/terraform")),
    ]
    for name in required_tools:
        try:
            path = tools.resolver.resolve(name)
        except ExecutableNotFoundError:
            items.append(DoctorItem(name, False, "not found on PATH"))
        else:
            items.append(DoctorItem(name, True, str(path)))
    return items


def docker_health(tools: Toolkit, *, env: Mapping[str, str]) -> DoctorItem:
    try:
        version = tools.docker.version(env=env).stdout.strip().splitlines()[0]
    except DeploymentError as exc:
        return DoctorItem("docker engine", False, str(exc))
    return DoctorItem("docker engine", True, version or "reachable")
