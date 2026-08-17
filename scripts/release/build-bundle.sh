#!/usr/bin/env bash
# Build a local release bundle for inspection: builds the project-code and
# superset images locally, resolves their local image digests, and writes the
# component manifest, compatibility matrix, catalog copy, CHANGELOG excerpt,
# SBOMs (when syft is available), and checksums.txt into RELEASE_BUNDLE_DIR.
#
# This never pushes an image or publishes anything; it mirrors the
# .github/workflows/release.yml pipeline locally so a maintainer can inspect
# exactly what a release would contain before cutting a tag. The manifest
# and checksums logic is owned by `olf release ...` (tools/olf/olf/release.py)
# per ADR 0017; this script only orchestrates docker/syft CLI calls and file
# placement.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

RELEASE_BUNDLE_DIR="${RELEASE_BUNDLE_DIR:-.tmp/release-bundle}"
GIT_SHA="$(git rev-parse HEAD 2>/dev/null || echo unknown)"

if ! command -v docker &>/dev/null; then
  echo "ERROR: 'docker' not found on PATH" >&2
  exit 1
fi

echo "==> Building local images for the release bundle"
project_code_base_image="$(uv run --project tools/olf --locked python -c "
import yaml
print(yaml.safe_load(open('release/component-catalog.yaml'))['components']['images']['project_code_base'])
")"
superset_base_image="$(uv run --project tools/olf --locked python -c "
import yaml
print(yaml.safe_load(open('release/component-catalog.yaml'))['components']['images']['superset_base'])
")"
PROJECT_CODE_IMAGE_TAG="bundle" PROJECT_CODE_PYTHON_BASE_IMAGE="${project_code_base_image}" make project-code-image
SUPERSET_IMAGE_TAG="bundle" SUPERSET_BASE_IMAGE="${superset_base_image}" make superset-image

project_code_ref="ghcr.io/openlakeforge/project-code:bundle"
superset_ref="ghcr.io/openlakeforge/superset:bundle"

echo "==> Resolving local image digests"
project_code_digest="$(docker image inspect --format='{{.Id}}' "${project_code_ref}")"
superset_digest="$(docker image inspect --format='{{.Id}}' "${superset_ref}")"

version="$(uv run --project tools/olf --locked python -c "
import yaml
print(yaml.safe_load(open('release/component-catalog.yaml'))['distribution']['version'])
")"

# Retag with the plain catalog version so 'repo:version' resolves to this
# exact local digest -- mirroring what the real release pipeline's
# `docker buildx imagetools create -t "<repo>:<version>"` step guarantees for
# a published tag (.github/workflows/release.yml). This is what lets
# `olf install run` (tools/olf/olf/install.py) deploy from this local
# rehearsal bundle the same way it deploys a real one; never pushed anywhere.
docker tag "${project_code_ref}" "ghcr.io/openlakeforge/project-code:${version}"
docker tag "${superset_ref}" "ghcr.io/openlakeforge/superset:${version}"

rm -rf "${RELEASE_BUNDLE_DIR}"
mkdir -p "${RELEASE_BUNDLE_DIR}"

echo "==> Writing component manifest"
uv run --project tools/olf --locked olf release manifest \
  --git-sha "${GIT_SHA}" \
  --image "project-code=${project_code_ref%:bundle}@${project_code_digest}" \
  --image "superset=${superset_ref%:bundle}@${superset_digest}" \
  --output "${RELEASE_BUNDLE_DIR}/component-manifest.json"

echo "==> Writing compatibility matrix"
uv run --project tools/olf --locked olf release compatibility-matrix \
  --output "${RELEASE_BUNDLE_DIR}/compatibility-matrix.md"

echo "==> Copying catalog and changelog"
cp release/component-catalog.yaml "${RELEASE_BUNDLE_DIR}/component-catalog.yaml"
cp CHANGELOG.md "${RELEASE_BUNDLE_DIR}/CHANGELOG.md"

if command -v syft &>/dev/null; then
  echo "==> Generating SBOMs with syft"
  syft "${project_code_ref}" -o spdx-json="${RELEASE_BUNDLE_DIR}/project-code.spdx.json"
  syft "${superset_ref}" -o spdx-json="${RELEASE_BUNDLE_DIR}/superset.spdx.json"
else
  echo "WARN: 'syft' not found on PATH; skipping local SBOM generation." >&2
  echo "      Install from https://github.com/anchore/syft to generate SBOMs locally." >&2
fi

echo "==> Building install bundle"
INSTALL_BUNDLE_OUTPUT="${RELEASE_BUNDLE_DIR}/install-bundle.tar.gz" bash "${SCRIPT_DIR}/build-install-bundle.sh"

echo "==> Writing checksums.txt"
uv run --project tools/olf --locked olf release checksums --dir "${RELEASE_BUNDLE_DIR}"

echo "Release bundle written to ${RELEASE_BUNDLE_DIR}"
