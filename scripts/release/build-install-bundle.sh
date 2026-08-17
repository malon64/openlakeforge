#!/usr/bin/env bash
# Build the deterministic install-bundle.tar.gz release asset: the exact
# deploy inputs `olf install run` needs to apply a tagged release into an
# existing cluster with no repository clone -- the same Terraform roots, Helm
# values, domain/lib code, phase scripts, and tools/olf that `make local-up`
# deploys from (ADR 0024). Nothing here is regenerated or transformed, only
# tarred, so the maintainer and consumer paths cannot drift.
#
# Usage:
#   scripts/release/build-install-bundle.sh [OUTPUT_PATH]
#
# OUTPUT_PATH defaults to INSTALL_BUNDLE_OUTPUT, or
# .tmp/release-bundle/install-bundle.tar.gz. The archive's single top-level
# directory is named openlakeforge-<distribution.version>/, matching
# release/component-catalog.yaml.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

OUTPUT_PATH="${1:-${INSTALL_BUNDLE_OUTPUT:-.tmp/release-bundle/install-bundle.tar.gz}}"
if [[ "${OUTPUT_PATH}" != /* ]]; then
  OUTPUT_PATH="${REPO_ROOT}/${OUTPUT_PATH}"
fi

# The exact deploy-input tree, mirrored verbatim -- see the module docstring
# in tools/olf/olf/install.py for how olf install run drives it after unpack.
bundle_paths=(
  "infra/terraform/environments/local"
  "infra/terraform/modules"
  "infra/helm/values/local"
  "domains"
  "libs"
  "scripts/lib"
  "scripts/contracts"
  "scripts/artifacts"
  "scripts/local/stack"
  "tools/olf"
  "release/component-catalog.yaml"
)

for path in "${bundle_paths[@]}"; do
  if [[ ! -e "${path}" ]]; then
    echo "ERROR: install bundle input path does not exist: ${path}" >&2
    exit 1
  fi
done

version="$(uv run --project tools/olf --locked python -c "
import yaml
print(yaml.safe_load(open('release/component-catalog.yaml'))['distribution']['version'])
")"

mkdir -p "$(dirname "${OUTPUT_PATH}")"

# Enumerate git-tracked files only: a raw filesystem walk would also sweep up
# gitignored local state (.terraform/ provider plugin caches, .venv/, editor
# state) that must never ship in a release asset -- large, platform-specific,
# and non-deterministic across machines.
tracked_files="$(git -C "${REPO_ROOT}" ls-files -- "${bundle_paths[@]}")"
if [[ -z "${tracked_files}" ]]; then
  echo "ERROR: no git-tracked files found under the install bundle paths" >&2
  exit 1
fi

echo "==> Building ${OUTPUT_PATH} (openlakeforge-${version}/)"
INSTALL_BUNDLE_OUTPUT_PATH="${OUTPUT_PATH}" \
INSTALL_BUNDLE_PREFIX="openlakeforge-${version}" \
INSTALL_BUNDLE_FILES="${tracked_files}" \
  uv run --project tools/olf --locked python -c "
import gzip
import os
import tarfile

# Deterministic (sorted, fixed timestamp/owner) so the checksum is
# reproducible from the same source tree. This has to cover the gzip
# wrapper too, not just the tar member headers: tarfile's 'w:gz' mode
# writes the current wall-clock time into the gzip header's MTIME field by
# default, which would make two builds from an identical tree produce
# different bytes (and a different sha256) even with every tar member
# timestamp pinned. filename='' also suppresses gzip's embedded original-
# filename field, the other source of non-determinism in the wrapper.
_FIXED_MTIME = 1577836800  # 2020-01-01T00:00:00Z


def _reset(tarinfo):
    tarinfo.uid = 0
    tarinfo.gid = 0
    tarinfo.uname = ''
    tarinfo.gname = ''
    tarinfo.mtime = _FIXED_MTIME
    return tarinfo


output_path = os.environ['INSTALL_BUNDLE_OUTPUT_PATH']
prefix = os.environ['INSTALL_BUNDLE_PREFIX']
files = sorted(os.environ['INSTALL_BUNDLE_FILES'].splitlines())

with open(output_path, 'wb') as raw_file:
    with gzip.GzipFile(filename='', mode='wb', fileobj=raw_file, mtime=_FIXED_MTIME, compresslevel=9) as gz:
        with tarfile.open(fileobj=gz, mode='w') as archive:
            for path in files:
                archive.add(path, arcname=os.path.join(prefix, path), filter=_reset, recursive=False)
"

echo "Wrote ${OUTPUT_PATH}"
