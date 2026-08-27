"""Docker build contexts for the project-code image."""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from olf.deployment.context import DeploymentPaths


@contextmanager
def project_code_build_context(paths: DeploymentPaths) -> Iterator[Path]:
    """Yield a context with platform build assets and the selected project.

    The immutable distribution supplies the Dockerfile, dependency lock and
    shared libraries.  A writable ``--project-root`` supplies only product
    code, so its ``lakehouse_code`` must replace the bundled demo before the
    image is built.  Source mode has one tree already and needs no staging.
    """
    if paths.project.root == paths.project.distribution_root:
        yield paths.distribution_root
        return

    paths.work_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="project-code-context-", dir=paths.work_root) as temporary:
        root = Path(temporary)
        for relative in ("images/project-code", "packages/domain-model", "libs"):
            shutil.copytree(paths.distribution_root / relative, root / relative)
        shutil.copytree(paths.project.code_root, root / "lakehouse_code")
        yield root
