"""Provider-neutral product Floe manifest generation.

Port of `scripts/artifacts/floe-manifest.sh`, kept out of
`olf.deployment.local` so the AWS/Azure providers (#125) reuse it
unmodified. Only the branches the Python-orchestrated providers actually
take are ported here (`PERSIST_RUNTIME_ARTIFACTS=true`, no `FLOE_PROFILE_
PATH`/`FLOE_CONFIG_PATH`/`FLOE_MANIFEST_PATH`/`FLOE_REMOTE_RUNTIME_BASE_URI`
override); the native-CLI runner mode stays in the shell script, which
remains in place for `make floe-manifest`.

The one behavioral difference between providers is profile selection, which
is why it's a `ProfileStrategy` seam rather than a branch inside
`generate_manifests`:

- Local and Azure (`RenderedProfileStrategy`) copy the same pre-resolved
  `local-k8s.yml` profile into every product's profile directory.
- AWS (`AwsGlueProfileStrategy`) renders a *fresh* profile per product,
  pointed at that product's own Glue Silver database - port of
  `profile_for_config`'s AWS branch in the shell script.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from olf import floe as floe_module
from olf import log
from olf.deployment.engine import Toolkit
from olf.deployment.env_settings import env as _env
from olf.deployment.errors import DeploymentPreconditionError

_PROFILE_FILENAME = "local-k8s.yml"
_CHECKED_IN_PROFILE_RELATIVE_PATH = Path("libs/floe/profiles") / _PROFILE_FILENAME
_AWS_PROFILE_FILENAME = "aws-eks.yml"
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
class FloeManifestSettings:
    version: str
    image: str
    runtime: str
    runtime_artifact_dir: Path
    platform: str | None

    @classmethod
    def from_environment(
        cls, environ: Mapping[str, str], *, repo_root: Path, scope: str = "local"
    ) -> FloeManifestSettings:
        version = _env(environ, "FLOE_VERSION", "0.6.11")
        default_runtime_dir = repo_root / f".tmp/floe-runtime/{scope}"
        return cls(
            version=version,
            image=_env(environ, "FLOE_IMAGE", f"ghcr.io/malon64/floe:{version}"),
            runtime=_env(environ, "FLOE_RUNTIME", "image"),
            runtime_artifact_dir=Path(_env(environ, "FLOE_RUNTIME_ARTIFACT_DIR", str(default_runtime_dir))),
            platform=environ.get("FLOE_PLATFORM") or None,
        )


@dataclass(frozen=True)
class _GeneratedManifest:
    domain: str
    product: str
    manifest_path: Path


class ProfileStrategy(Protocol):
    def profile_for(self, *, domain: str, product: str, profile_dir: Path) -> Path:
        """Write this product's profile into `profile_dir` and return its path."""


@dataclass(frozen=True)
class RenderedProfileStrategy:
    """Copies one pre-resolved profile into every product's profile directory.

    Used by local and Azure - both consume the checked-in or rendered
    `local-k8s.yml` profile from `_resolve_base_profile`, unchanged per
    product.
    """

    base_profile: Path

    def profile_for(self, *, domain: str, product: str, profile_dir: Path) -> Path:  # noqa: ARG002
        profile_dir.mkdir(parents=True, exist_ok=True)
        profile_path = profile_dir / self.base_profile.name
        profile_path.write_text(self.base_profile.read_text())
        return profile_path


@dataclass(frozen=True)
class AwsGlueProfileStrategy:
    """Renders a fresh profile per product, pointed at that product's Glue database.

    Port of `scripts/artifacts/floe-manifest.sh::profile_for_config`'s AWS
    Glue branch: each product's profile sets `OPENLAKEFORGE_CATALOG_GLUE_
    DATABASE` to its own Silver namespace, read from `OPENLAKEFORGE_CATALOG_
    SILVER_NAMESPACES_JSON`.
    """

    environ: Mapping[str, str]
    profile_filename: str = _AWS_PROFILE_FILENAME

    def profile_for(self, *, domain: str, product: str, profile_dir: Path) -> Path:
        product_key = f"{domain}_{product}"
        silver_namespace = self._silver_namespace_for_product(product_key)
        log.step(f"Rendering AWS Floe profile for {product_key} with Glue database {silver_namespace}...")
        profile_dir.mkdir(parents=True, exist_ok=True)
        profile_path = profile_dir / self.profile_filename
        rendered_environ = {**self.environ, "OPENLAKEFORGE_CATALOG_GLUE_DATABASE": silver_namespace}
        profile_path.write_text(floe_module.render_profile(rendered_environ))
        return profile_path

    def _silver_namespace_for_product(self, product_key: str) -> str:
        raw = self.environ.get("OPENLAKEFORGE_CATALOG_SILVER_NAMESPACES_JSON", "{}")
        try:
            namespaces = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DeploymentPreconditionError(
                f"invalid OPENLAKEFORGE_CATALOG_SILVER_NAMESPACES_JSON: {exc}"
            ) from exc
        namespace = namespaces.get(product_key)
        if not namespace:
            raise DeploymentPreconditionError(f"missing Silver catalog namespace for product {product_key}")
        return namespace


def discover_floe_configs(repo_root: Path) -> list[Path]:
    return sorted((repo_root / "domains").glob("*/contracts/floe/*.yml"))


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


def _prepare_runtime_root_and_discover_configs(settings: FloeManifestSettings, *, repo_root: Path) -> list[Path]:
    """Wipe and recreate the runtime artifact tree, then discover product configs.

    Must run before any profile is resolved into `settings.runtime_artifact_
    dir` - the wipe would otherwise delete a profile written before this
    call, which is exactly the ordering `_resolve_base_profile` relies on.
    """
    runtime_root = settings.runtime_artifact_dir
    if runtime_root.exists():
        shutil.rmtree(runtime_root)
    for subdirectory in ("configs", "manifests", "profiles"):
        (runtime_root / subdirectory).mkdir(parents=True, exist_ok=True)

    configs = discover_floe_configs(repo_root)
    if not configs:
        raise DeploymentPreconditionError("no product Floe configs found under domains/*/contracts/floe/*.yml.")
    return configs


def _generate_manifests_for_configs(
    configs: list[Path],
    settings: FloeManifestSettings,
    tools: Toolkit,
    *,
    repo_root: Path,
    profile_strategy: ProfileStrategy,
    environ: Mapping[str, str],
    env: Mapping[str, str],
) -> list[Path]:
    runtime_root = settings.runtime_artifact_dir
    env_names = [name for name in _AWS_PASSTHROUGH_ENV_NAMES if environ.get(name)]
    generated: list[_GeneratedManifest] = []

    for config_path in configs:
        domain_dir = config_path.parent.parent.parent
        domain = domain_dir.name
        product = config_path.stem

        profile_dir = runtime_root / "profiles" / domain / product
        profile_path = profile_strategy.profile_for(domain=domain, product=product, profile_dir=profile_dir)

        runtime_config_dir = runtime_root / "configs" / domain / product
        runtime_config_dir.mkdir(parents=True, exist_ok=True)
        runtime_config_path = runtime_config_dir / config_path.name
        runtime_config_path.write_text(config_path.read_text())

        manifest_dir = runtime_root / "manifests" / domain / product
        manifest_dir.mkdir(parents=True, exist_ok=True)
        # The Floe image runs as its own non-root user. Give that user
        # access to the bind-mounted output directory, or manifest
        # generation fails with EACCES on hosted CI runners.
        manifest_dir.chmod(0o777)
        manifest_path = manifest_dir / f"{product}.manifest.json"

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
                f"{domain}.{product}.local",
                "--default-domain",
                f"{domain}_{product}",
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
        generated.append(_GeneratedManifest(domain=domain, product=product, manifest_path=manifest_path))

    return [item.manifest_path for item in generated]


def generate_manifests(
    settings: FloeManifestSettings,
    tools: Toolkit,
    *,
    repo_root: Path,
    profile_strategy: ProfileStrategy,
    environ: Mapping[str, str],
    env: Mapping[str, str],
) -> list[Path]:
    configs = _prepare_runtime_root_and_discover_configs(settings, repo_root=repo_root)
    return _generate_manifests_for_configs(
        configs, settings, tools, repo_root=repo_root, profile_strategy=profile_strategy, environ=environ, env=env
    )


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
    configs = _prepare_runtime_root_and_discover_configs(settings, repo_root=repo_root)
    base_profile = _resolve_base_profile(
        settings, repo_root=repo_root, namespace=namespace, governance_enabled=governance_enabled, environ=environ
    )
    strategy = RenderedProfileStrategy(base_profile=base_profile)
    return _generate_manifests_for_configs(
        configs, settings, tools, repo_root=repo_root, profile_strategy=strategy, environ=environ, env=env
    )


def generate_aws_manifests(
    settings: FloeManifestSettings,
    tools: Toolkit,
    *,
    repo_root: Path,
    environ: Mapping[str, str],
    env: Mapping[str, str],
) -> list[Path]:
    strategy = AwsGlueProfileStrategy(environ=environ)
    return generate_manifests(
        settings, tools, repo_root=repo_root, profile_strategy=strategy, environ=environ, env=env
    )
