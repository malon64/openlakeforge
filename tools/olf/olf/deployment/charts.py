"""Provider-neutral Helm chart cache management.

Port of `scripts/lib/helm.sh::prepare_cached_chart` and
`prepare_cached_dagster_chart_no_schema`. Kept out of `olf.deployment.local`
so the AWS/Azure providers (#125) reuse both verbatim - the Dagster chart
ships a `values.schema.json` that rejects the OpenLakeForge values overlay,
so the cached package is re-packed without it, exactly as the shell helper
did.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

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
    sha256: str | None = None


@dataclass(frozen=True)
class CatalogChart:
    """An immutable third-party chart declaration from the component catalog."""

    name: str
    repository: str
    reference: str
    version: str
    sha256: str

    @classmethod
    def load(cls, catalog_path: Path, name: str) -> CatalogChart:
        try:
            catalog = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
            value = catalog["components"]["helm"]["charts"][name]
        except (KeyError, OSError, TypeError, yaml.YAMLError) as exc:
            raise ValueError(f"chart {name!r} is not declared in {catalog_path}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"chart {name!r} in {catalog_path} must be a mapping")
        fields = {field: value.get(field) for field in ("repository", "reference", "version", "sha256")}
        if not all(isinstance(item, str) and item for item in fields.values()):
            raise ValueError(f"chart {name!r} in {catalog_path} has incomplete immutable metadata")
        digest = fields["sha256"]
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError(f"chart {name!r} in {catalog_path} has an invalid SHA-256")
        return cls(name=name, **fields)  # type: ignore[arg-type]

    def request(self, *, cache_root: Path) -> ChartRequest:
        return ChartRequest(
            display_name=self.name,
            repo_name=self.reference.split("/", 1)[0],
            repo_url=self.repository,
            chart_ref=self.reference,
            version=self.version,
            package_path=cache_root / "helm" / f"{self.sha256}.tgz",
            sha256=self.sha256,
        )


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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

    cache_has_expected_digest = request.package_path.is_file() and (
        request.sha256 is None or _digest(request.package_path) == request.sha256
    )
    cache_has_valid_chart = cache_has_expected_digest and helm.show_chart(
        request.package_path,
        repository_config=repository_config,
        repository_cache=repository_cache,
        env=env,
    ).ok
    if cache_has_valid_chart:
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
    if request.sha256 is None:
        return request.package_path

    chart_name = request.chart_ref.rsplit("/", 1)[-1]
    downloaded = paths.helm_cache_dir / f"{chart_name}-{request.version}.tgz"
    if not downloaded.is_file() or _digest(downloaded) != request.sha256:
        downloaded.unlink(missing_ok=True)
        raise ValueError(f"{request.display_name} chart digest does not match the component catalog")
    request.package_path.parent.mkdir(parents=True, exist_ok=True)
    staged = request.package_path.with_name(f".{request.sha256}.{os.getpid()}.tgz")
    downloaded.replace(staged)
    os.replace(staged, request.package_path)
    return request.package_path


def prepare_cached_dagster_chart_no_schema(
    request: ChartRequest,
    *,
    helm: Helm,
    paths: DeploymentPaths,
    env: Mapping[str, str],
    retry_policy: RetryPolicy,
) -> Path:
    """Cache the Dagster Helm chart with `values.schema.json` stripped.

    Port of `scripts/lib/helm.sh::prepare_cached_dagster_chart_no_schema`: the
    upstream Dagster chart ships a schema that rejects the OpenLakeForge
    values overlay, so the cached package is untarred, has every
    `values.schema.json` deleted, and is re-packaged under `request.
    package_path` (distinct from the plain `helm pull` output name, since
    that name collides with `prepare_cached_chart`'s Dagster-less callers).
    """
    repository_config = paths.helm_repository_config
    repository_cache = paths.helm_repository_cache

    if request.package_path.is_file() and helm.show_chart(
        request.package_path, repository_config=repository_config, repository_cache=repository_cache, env=env
    ).ok:
        log.step(f"Using cached Dagster Helm chart: {request.package_path}")
        return request.package_path

    request.package_path.unlink(missing_ok=True)

    chart_name = request.chart_ref.rsplit("/", 1)[-1]
    paths.work_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dagster-chart.", dir=paths.work_root) as work_dir_str:
        work_dir = Path(work_dir_str)

        log.step(f"Downloading Dagster Helm chart {request.version} into local cache...")
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
            untar=True,
            untar_dir=work_dir,
            repository_config=repository_config,
            repository_cache=repository_cache,
            env=env,
            retry_policy=retry_policy,
        )

        chart_dir = work_dir / chart_name
        for schema_file in chart_dir.rglob("values.schema.json"):
            schema_file.unlink()

        paths.helm_cache_dir.mkdir(parents=True, exist_ok=True)
        helm.package(chart_dir, destination=paths.helm_cache_dir, env=env)
        packaged_path = paths.helm_cache_dir / f"{chart_name}-{request.version}.tgz"
        request.package_path.parent.mkdir(parents=True, exist_ok=True)
        packaged_path.rename(request.package_path)

    return request.package_path
