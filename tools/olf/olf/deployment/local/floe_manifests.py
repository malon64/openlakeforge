"""Local domain Floe manifest generation.

Minimal, local-only port of `scripts/artifacts/floe-manifest.sh`. Only the
branches that path actually takes for the local (non-AWS, non-Glue,
`PERSIST_RUNTIME_ARTIFACTS=true`, no `FLOE_PROFILE_PATH`/`FLOE_CONFIG_PATH`/
`FLOE_MANIFEST_PATH`/`FLOE_REMOTE_RUNTIME_BASE_URI` override) path are ported
here; the cloud/AWS-Glue behavior and the native-CLI runner mode stay in the
shell script, which remains in place for `make floe-manifest` and the
AWS/Azure paths.
"""

from __future__ import annotations

import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from olf import floe as floe_module
from olf import log
from olf.deployment.engine import Toolkit
from olf.deployment.errors import DeploymentPreconditionError
from olf.deployment.local.config import FloeManifestSettings

_PROFILE_FILENAME = "local-k8s.yml"
_CHECKED_IN_PROFILE_RELATIVE_PATH = Path("libs/floe/profiles") / _PROFILE_FILENAME
_AWS_PASSTHROUGH_ENV_NAMES = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_REGION",
    "AWS_DEFAULT_REGION",
    "AWS_ENDPOINT_URL",
    "AWS_ENDPOINT_URL_S3",
    "AWS_S3_FORCE_PATH_STYLE",
    "AWS_ALLOW_HTTP",
    "AWS_EC2_METADATA_DISABLED",
)


@dataclass(frozen=True)
class _GeneratedManifest:
    domain: str
    manifest_path: Path


def discover_floe_configs(repo_root: Path) -> list[Path]:
    return sorted((repo_root / "lakehouse_code" / "silver").glob("*/contracts/floe/*.yml"))


def _domain_for_config(config_path: Path) -> str:
    return config_path.parents[2].name


def _validate_one_config_per_domain(configs: list[Path]) -> None:
    domains = [_domain_for_config(config_path) for config_path in configs]
    duplicates = sorted({domain for domain in domains if domains.count(domain) > 1})
    if duplicates:
        raise DeploymentPreconditionError(
            "each domain must have exactly one Floe configuration under "
            "lakehouse_code/silver/<domain>/contracts/floe/; duplicate configs found for: "
            + ", ".join(duplicates)
        )


def _container_path(repo_root: Path, path: Path) -> str:
    resolved = path if path.is_absolute() else repo_root / path
    if resolved == repo_root:
        return "/work"
    try:
        relative = resolved.relative_to(repo_root)
    except ValueError:
        return str(resolved)
    return f"/work/{relative}"


def _resolve_base_profile(
    settings: FloeManifestSettings,
    *,
    repo_root: Path,
    namespace: str,
    governance_enabled: bool,
    environ: Mapping[str, str],
) -> Path:
    if namespace == "lakehouse" and governance_enabled:
        return repo_root / _CHECKED_IN_PROFILE_RELATIVE_PATH

    profiles_dir = settings.runtime_artifact_dir / "profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)
    rendered_path = profiles_dir / _PROFILE_FILENAME
    rendered_path.write_text(floe_module.render_profile(environ))
    return rendered_path


def generate_local_manifests(
    settings: FloeManifestSettings,
    tools: Toolkit,
    *,
    repo_root: Path,
    namespace: str,
    governance_enabled: bool,
    environ: Mapping[str, str],
    env: Mapping[str, str],
) -> list[Path]:
    runtime_root = settings.runtime_artifact_dir
    if runtime_root.exists():
        shutil.rmtree(runtime_root)
    for subdirectory in ("configs", "manifests", "profiles"):
        (runtime_root / subdirectory).mkdir(parents=True, exist_ok=True)

    configs = discover_floe_configs(repo_root)
    if not configs:
        raise DeploymentPreconditionError(
            "no domain Floe configs found under lakehouse_code/silver/*/contracts/floe/*.yml."
        )
    _validate_one_config_per_domain(configs)

    base_profile = _resolve_base_profile(
        settings, repo_root=repo_root, namespace=namespace, governance_enabled=governance_enabled, environ=environ
    )

    env_names = [name for name in _AWS_PASSTHROUGH_ENV_NAMES if environ.get(name)]
    generated: list[_GeneratedManifest] = []

    for config_path in configs:
        domain = _domain_for_config(config_path)

        profile_dir = runtime_root / "profiles" / domain
        profile_dir.mkdir(parents=True, exist_ok=True)
        profile_path = profile_dir / base_profile.name
        profile_path.write_text(base_profile.read_text())

        runtime_config_dir = runtime_root / "configs" / domain
        runtime_config_dir.mkdir(parents=True, exist_ok=True)
        runtime_config_path = runtime_config_dir / config_path.name
        runtime_config_path.write_text(config_path.read_text())

        manifest_dir = runtime_root / "manifests" / domain
        manifest_dir.mkdir(parents=True, exist_ok=True)
        # The Floe image runs as its own non-root user. Give that user
        # access to the bind-mounted output directory, or manifest
        # generation fails with EACCES on hosted CI runners.
        manifest_dir.chmod(0o777)
        manifest_path = manifest_dir / f"{domain}.manifest.json"

        floe_config_path = _container_path(repo_root, runtime_config_path)
        floe_profile_path = _container_path(repo_root, profile_path)
        floe_manifest_path = _container_path(repo_root, manifest_path)

        log.step(f"Validating Floe config: {config_path}")
        tools.docker.run_container(
            settings.image,
            ["validate", "-c", floe_config_path, "-p", floe_profile_path],
            platform=settings.platform,
            env_names=env_names,
            volumes=[f"{repo_root}:/work"],
            workdir="/",
            env=env,
        )

        log.step(f"Generating Floe manifest: {manifest_path}")
        tools.docker.run_container(
            settings.image,
            [
                "manifest",
                "generate",
                "-c",
                floe_config_path,
                "-p",
                floe_profile_path,
                "--deterministic",
                "--manifest-name",
                f"{domain}.local",
                "--default-domain",
                domain,
                "--manifest-path-mode",
                "resolved-uri",
                "--runtime",
                settings.runtime,
                "--output",
                floe_manifest_path,
            ],
            platform=settings.platform,
            env_names=env_names,
            volumes=[f"{repo_root}:/work"],
            workdir="/",
            env=env,
        )
        log.step(f"Generated {manifest_path}")
        generated.append(_GeneratedManifest(domain=domain, manifest_path=manifest_path))

    return [item.manifest_path for item in generated]
