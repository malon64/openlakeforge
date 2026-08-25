"""Local Superset and project-code image build/load.

Port of `scripts/local/images/{build,load}-{superset,project-code}.sh`. The
`kind`-less Docker-node fallback in the shell `load-*.sh` scripts is not
ported here: the local provider already hard-requires `kind` (kubeconfig
export, `kind get nodes` during prefetch), and the shell scripts remain in
place, untouched, for the standalone `make superset-load`/`project-code-load`
targets.
"""

from __future__ import annotations

from collections.abc import Mapping

from olf import log
from olf.deployment.engine import Toolkit
from olf.deployment.errors import DeploymentPreconditionError
from olf.deployment.local.config import LocalDeploymentConfig


def build_superset_image(config: LocalDeploymentConfig, tools: Toolkit, *, env: Mapping[str, str]) -> str:
    images = config.images
    log.step(f"Pulling Superset base image: {images.superset_base_image}")
    tools.docker.pull(images.superset_base_image, env=env, retry_policy=images.pull_retry)

    log.step(f"Building Superset image: {images.superset_image}")
    tools.docker.build(
        config.paths.distribution_root / "images/superset",
        tag=images.superset_image,
        file=config.paths.distribution_root / "images/superset/Dockerfile",
        build_args={"SUPERSET_BASE_IMAGE": images.superset_base_image},
        env=env,
        retry_policy=images.build_retry,
    )
    return images.superset_image


def build_project_code_image(
    config: LocalDeploymentConfig,
    tools: Toolkit,
    *,
    env: Mapping[str, str],
    revision: str,
) -> str:
    images = config.images
    log.step(f"Pulling project-code Python base image: {images.project_code_python_base_image}")
    tools.docker.pull(images.project_code_python_base_image, env=env, retry_policy=images.pull_retry)

    log.step(f"Building project-code image: {images.project_code_image}")
    tools.docker.build(
        config.paths.distribution_root,
        tag=images.project_code_image,
        file=config.paths.distribution_root / "images/project-code/Dockerfile",
        build_args={
            "PYTHON_BASE_IMAGE": images.project_code_python_base_image,
            "DBT_PROFILE_ENV": images.project_code_dbt_profile_env,
            "FLOE_MANIFEST_REVISION": revision,
        },
        env=env,
        retry_policy=images.build_retry,
    )
    return images.project_code_image


def load_image_into_kind(image: str, config: LocalDeploymentConfig, tools: Toolkit, *, env: Mapping[str, str]) -> None:
    if not tools.docker.image_exists(image, env=env):
        raise DeploymentPreconditionError(f"local image '{image}' does not exist. Build it first.")
    if config.cluster.name not in tools.kind.get_clusters(env=env):
        raise DeploymentPreconditionError(
            f"kind cluster '{config.cluster.name}' does not exist. Run the foundation phase first."
        )
    log.step(f"Loading {image} into kind cluster '{config.cluster.name}'")
    tools.kind.load_docker_image(image, cluster_name=config.cluster.name, env=env)
    log.step(f"Loaded {image}")


def prepare_superset_image(config: LocalDeploymentConfig, tools: Toolkit, *, env: Mapping[str, str]) -> None:
    if not config.features.analytics_enabled:
        return
    if config.images.superset_tag != "local":
        return
    log.step("Building local Superset platform image...")
    image = build_superset_image(config, tools, env=env)
    log.step("Ensuring local Superset platform image is available to kind...")
    load_image_into_kind(image, config, tools, env=env)


def prepare_project_code_image(
    config: LocalDeploymentConfig,
    tools: Toolkit,
    *,
    env: Mapping[str, str],
    revision: str,
) -> None:
    if config.images.project_code_tag != "local":
        return
    log.step("Building local project-code image...")
    image = build_project_code_image(config, tools, env=env, revision=revision)
    log.step("Ensuring local project-code image is available to kind...")
    load_image_into_kind(image, config, tools, env=env)
