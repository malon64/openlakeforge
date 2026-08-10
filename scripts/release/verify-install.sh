#!/usr/bin/env bash
# Clean-checkout install verification for a published OpenLakeForge release.
#
# Run this against a tag that has already been published by
# .github/workflows/release.yml (i.e. a real, non-dry-run release). It
# proves the three acceptance-criteria claims in docs/release/releasing.md:
#
#   1. Signatures verify:  cosign verify against the published image digests.
#   2. Checksum manifest:  keyless cosign verification authenticates the
#                          manifest before sha256sum -c checks the assets.
#   3. Clean checkout:     a fresh `git clone` at the release tag passes the
#                          repository's own structural/component consistency
#                          checks with no local state carried over.
#
# Usage:
#   scripts/release/verify-install.sh [TAG]
#
# TAG defaults to v<distribution.version> from release/component-catalog.yaml
# in the current checkout. Requires: git, curl or gh, cosign, sha256sum (or
# shasum on macOS), uv (https://docs.astral.sh/uv/, used by the cloned
# checkout's own scripts/test/check-components.sh), and docker (for the
# optional --pull-images smoke).
#
# This script is intentionally runnable by anyone outside CI: it downloads
# public release assets and verifies public Sigstore signatures, no
# credentials required.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
REPO_SLUG="${OPENLAKEFORGE_REPO_SLUG:-malon64/openlakeforge}"
WORK_DIR="${OPENLAKEFORGE_VERIFY_WORKDIR:-$(mktemp -d /tmp/openlakeforge-verify.XXXXXX)}"
PULL_IMAGES="false"

run_python() {
  uv run --project "${REPO_ROOT}/tools/olf" --locked python "$@"
}

for arg in "$@"; do
  case "${arg}" in
    --pull-images)
      PULL_IMAGES="true"
      ;;
    -*)
      echo "ERROR: unknown flag ${arg}" >&2
      exit 1
      ;;
    *)
      TAG="${arg}"
      ;;
  esac
done

if [[ -z "${TAG:-}" ]]; then
  TAG="v$(run_python -c "
import sys, yaml
print(yaml.safe_load(open(sys.argv[1]))['distribution']['version'])
" "${REPO_ROOT}/release/component-catalog.yaml")"
fi

echo "==> Verifying OpenLakeForge release ${TAG} (repo ${REPO_SLUG})"
echo "    Work directory: ${WORK_DIR}"

sha256_cmd() {
  if command -v sha256sum &>/dev/null; then
    sha256sum "$@"
  else
    shasum -a 256 "$@"
  fi
}

# Escape ERE metacharacters so a value used as a --certificate-identity-regexp
# fragment is matched literally -- an unescaped "." matches any character, so
# e.g. "v0.1.0-alpha.1" would also accept an identity ending in "v0x1x0-alphaX1".
regex_escape() {
  printf '%s' "$1" | sed -e 's/[].^$*+?(){}|[\]/\\&/g'
}

# --- 1. Download release assets -------------------------------------------------
mkdir -p "${WORK_DIR}/assets"
cd "${WORK_DIR}/assets"

echo "==> Downloading release assets for ${TAG}"
gh_downloaded="false"
if command -v gh &>/dev/null; then
  if gh release download "${TAG}" --repo "${REPO_SLUG}" --clobber; then
    gh_downloaded="true"
  else
    echo "    'gh' is installed but failed to download (not authenticated, or a network" >&2
    echo "    issue); falling back." >&2
  fi
fi

if [[ "${gh_downloaded}" == "true" ]]; then
  :
elif [[ -f "checksums.txt" ]]; then
  echo "    Assets already present in ${WORK_DIR}/assets -- using those."
elif command -v curl &>/dev/null; then
  echo "    Falling back to curl against the GitHub Releases API."
  api_url="https://api.github.com/repos/${REPO_SLUG}/releases/tags/${TAG}"
  release_json="$(curl -fsSL "${api_url}")" || {
    echo "ERROR: could not fetch release metadata for ${TAG} from ${api_url}" >&2
    exit 1
  }
  while IFS=$'\t' read -r asset_name asset_url; do
    [[ -z "${asset_name}" ]] && continue
    echo "    Downloading ${asset_name}..."
    curl -fsSL -o "${asset_name}" "${asset_url}"
  done < <(run_python -c "
import json, sys
data = json.loads(sys.argv[1])
for asset in data.get('assets', []):
    print(f\"{asset['name']}\t{asset['browser_download_url']}\")
" "${release_json}")
  if [[ ! -f "checksums.txt" ]]; then
    echo "ERROR: no checksums.txt found among the downloaded assets for ${TAG}" >&2
    exit 1
  fi
else
  echo "ERROR: could not download release assets ('gh' unavailable or failed, 'curl'" >&2
  echo "       not found), and no assets already present in ${WORK_DIR}/assets." >&2
  echo "       Install 'curl', authenticate 'gh', or download the release" >&2
  echo "       assets for ${TAG} from https://github.com/${REPO_SLUG}/releases/tag/${TAG}" >&2
  echo "       into ${WORK_DIR}/assets and re-run." >&2
  exit 1
fi

# --- 2. Authenticate and verify checksums ----------------------------------------
if ! command -v cosign &>/dev/null; then
  echo "ERROR: 'cosign' not found on PATH. Install from https://docs.sigstore.dev/cosign/installation/" >&2
  exit 1
fi

if [[ ! -f "checksums.txt.bundle" ]]; then
  echo "ERROR: checksums.txt.bundle not found among downloaded assets" >&2
  exit 1
fi

identity_regexp="^$(regex_escape "https://github.com/${REPO_SLUG}/.github/workflows/release.yml@refs/tags/${TAG}")\$"
echo "==> Authenticating checksums.txt with its keyless Sigstore bundle"
cosign verify-blob checksums.txt \
  --bundle checksums.txt.bundle \
  --certificate-identity-regexp "${identity_regexp}" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com"

echo "==> Verifying release asset checksums"
sha256_cmd -c checksums.txt
echo "    Authenticated checksums OK."

# --- 3. Verify image cosign signatures -------------------------------------------
manifest_json="component-manifest.json"
if [[ ! -f "${manifest_json}" ]]; then
  echo "ERROR: ${manifest_json} not found among downloaded assets" >&2
  exit 1
fi

for image in project-code superset; do
  reference="$(run_python -c "
import json
with open('${manifest_json}') as f:
    manifest = json.load(f)
print(manifest['resolved_images']['${image}'])
")"
  echo "==> Verifying cosign signature for ${reference}"
  cosign verify \
    --certificate-identity-regexp "${identity_regexp}" \
    --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
    "${reference}"
done
echo "    Signatures OK."

# --- 4. Clean-checkout structural verification ------------------------------------
echo "==> Cloning ${REPO_SLUG}@${TAG} into a clean checkout"
clone_dir="${WORK_DIR}/checkout"
rm -rf "${clone_dir}"
git clone --depth 1 --branch "${TAG}" "https://github.com/${REPO_SLUG}.git" "${clone_dir}"

# A force-moved or deleted-and-recreated tag would still pass the structural
# checks below (the newly checked-out tree is self-consistent) even though it
# no longer corresponds to the commit the downloaded assets/images were built
# from. Tie the checkout to the release it claims to verify.
cloned_sha="$(git -C "${clone_dir}" rev-parse HEAD)"
manifest_sha="$(run_python -c "
import json
with open('${manifest_json}') as f:
    manifest = json.load(f)
print(manifest['distribution']['git_sha'])
")"
if [[ "${cloned_sha}" != "${manifest_sha}" ]]; then
  echo "ERROR: tag ${TAG} now points at ${cloned_sha}, but the published release's" >&2
  echo "       component-manifest.json records distribution.git_sha=${manifest_sha}." >&2
  echo "       The tag moved after the release was published; the checked-out source" >&2
  echo "       no longer corresponds to the verified assets and images." >&2
  exit 1
fi
echo "    Checked-out commit ${cloned_sha} matches the manifest's recorded git_sha."

echo "==> Running repository structural/component checks from the clean checkout"
(
  cd "${clone_dir}"
  bash scripts/test/check-structure.sh
  bash scripts/test/check-components.sh
)
echo "    Clean-checkout structural checks OK."

# --- 5. Optional: pull and smoke-test the published images -----------------------
if [[ "${PULL_IMAGES}" == "true" ]]; then
  if ! command -v docker &>/dev/null; then
    echo "ERROR: --pull-images requires 'docker' on PATH" >&2
    exit 1
  fi
  for image in project-code superset; do
    reference="$(run_python -c "
import json
with open('${manifest_json}') as f:
    manifest = json.load(f)
print(manifest['resolved_images']['${image}'])
")"
    echo "==> Pulling ${reference}"
    docker pull "${reference}"
  done
  echo "    Image pull OK."
fi

echo ""
echo "OpenLakeForge ${TAG} verified: authenticated checksums OK, signatures OK, clean checkout OK."
