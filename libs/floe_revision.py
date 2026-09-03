"""Floe manifest revision resolution for the project-code runtime.

Kept free of Dagster/Floe imports so it can be unit tested from `tools/olf`
without the isolated project-code dependency environment `olf check
project-code` builds (`libs.product_dagster`, which owns the Dagster
definitions that consume this, cannot be imported there).
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

REVISION_ENV = "OPENLAKEFORGE_FLOE_MANIFEST_REVISION"
REVISION_BUILT_ENV = "OPENLAKEFORGE_FLOE_MANIFEST_REVISION_BUILT"
_REVISION_PATTERN = re.compile(r"^sha256:([0-9a-f]{64})$")


class ArtifactRevisionError(RuntimeError):
    """A project-code image cannot safely use its declared artifact revision."""


def built_manifest_revision(environ: Mapping[str, str] | None = None) -> str | None:
    """Return the active Floe revision for this project-code container.

    Prefers the stage-activated runtime value
    (`OPENLAKEFORGE_FLOE_MANIFEST_REVISION`) over the value baked into the
    image at build time (`OPENLAKEFORGE_FLOE_MANIFEST_REVISION_BUILT`).
    Without this preference the project-code image would need rebuilding per
    revision, which defeats #154's "one image digest deploys to every stage"
    contract.

    `olf project deploy` sets the runtime variable on every activation and
    `olf project image` deliberately builds with "manual", so the baked value
    is now only reached by the deprecated `olf deploy --phase artifacts` path.
    """
    env = environ if environ is not None else os.environ
    for env_name in (REVISION_ENV, REVISION_BUILT_ENV):
        revision = env.get(env_name, "").strip()
        if revision and revision != "manual":
            revision_digest(revision, env_name=env_name)
            return revision
    return None


def revision_digest(revision: str, *, env_name: str = REVISION_BUILT_ENV) -> str:
    match = _REVISION_PATTERN.fullmatch(revision)
    if match is None:
        raise ArtifactRevisionError(
            f"invalid {env_name} value {revision!r}; expected sha256:<64 lowercase hex characters> or 'manual'."
        )
    return match.group(1)
