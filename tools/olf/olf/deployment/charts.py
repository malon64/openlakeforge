"""Provider-neutral Helm chart cache management.

Port of `scripts/lib/helm.sh::prepare_cached_chart`. Kept out of
`olf.deployment.local` so #125 can reuse it verbatim for AWS/Azure charts.
`prepare_cached_dagster_chart_no_schema` is not ported here - it is only used
by the AWS/Azure platform scripts today, so it stays in `scripts/lib/helm.sh`
until #125.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from olf import log
from olf.deployment.context import DeploymentPaths
from olf.deployment.retry import RetryPolicy
from olf.tooling.helm import Helm


@dataclass(frozen=True)
class ChartRequest:
    display_name: str
    repo_name: str
    repo_url: str
    chart_ref: str
    version: str
    package_path: Path


def prepare_cached_chart(
    request: ChartRequest,
    *,
    helm: Helm,
    paths: DeploymentPaths,
    env: Mapping[str, str],
    retry_policy: RetryPolicy,
) -> Path:
    repository_config = paths.helm_repository_config
    repository_cache = paths.helm_repository_cache

    if request.package_path.is_file() and helm.show_chart(
        request.package_path, repository_config=repository_config, repository_cache=repository_cache, env=env
    ).ok:
        log.step(f"Using cached {request.display_name} Helm chart: {request.package_path}")
        return request.package_path

    request.package_path.unlink(missing_ok=True)

    log.step(f"Downloading {request.display_name} Helm chart {request.version} into local cache...")
    helm.repo_add(
        request.repo_name,
        request.repo_url,
        force_update=True,
        repository_config=repository_config,
        repository_cache=repository_cache,
        env=env,
        retry_policy=retry_policy,
    )
    helm.repo_update(
        repository_config=repository_config,
        repository_cache=repository_cache,
        env=env,
        retry_policy=retry_policy,
    )
    helm.pull(
        request.chart_ref,
        version=request.version,
        destination=paths.helm_cache_dir,
        repository_config=repository_config,
        repository_cache=repository_cache,
        env=env,
        retry_policy=retry_policy,
    )
    return request.package_path
