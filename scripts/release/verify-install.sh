#!/usr/bin/env bash
# Clean-checkout install verification for a published OpenLakeForge release.
#
# Run this against a tag that has already been published by
# .github/workflows/release.yml (i.e. a real, non-dry-run release). It
# proves the three acceptance-criteria claims in docs/release/releasing.md:
#
#   1. Signatures verify:  cosign verify against the published image digests.
#   2. Checksums verify:   sha256sum -c against the downloaded release assets.
#   3. Clean checkout:     a fresh `git clone` at the release tag passes the
#                          repository's own structural/component consistency
#                          checks with no local state carried over.
#
# Usage:
#   scripts/release/verify-install.sh [TAG]
#
# TAG defaults to v<distribution.version> from release/component-catalog.yaml
# in the current checkout. Requires: git, curl or gh, cosign, sha256sum (or
# shasum on macOS), and docker (for the optional --pull-images smoke).
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
  TAG="v$(cd "${REPO_ROOT}" && uv run --project tools/olf --locked python -c "
import yaml
print(yaml.safe_load(open('release/component-catalog.yaml'))['distribution']['version'])
")"
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

# --- 1. Download release assets -------------------------------------------------
mkdir -p "${WORK_DIR}/assets"
cd "${WORK_DIR}/assets"

echo "==> Downloading release assets for ${TAG}"
if command -v gh &>/dev/null; then
  gh release download "${TAG}" --repo "${REPO_SLUG}" --clobber
elif [[ -f "checksums.txt" ]]; then
  # A consumer who already downloaded every asset manually (as the previous
  # error message here used to suggest, without this script ever actually
  # checking for them) can re-run and proceed with what's already present.
  echo "    'gh' not found, but assets already present in ${WORK_DIR}/assets -- using those."
elif command -v curl &>/dev/null; then
  echo "    'gh' not found; falling back to curl against the GitHub Releases API."
  api_url="https://api.github.com/repos/${REPO_SLUG}/releases/tags/${TAG}"
  release_json="$(curl -fsSL "${api_url}")" || {
    echo "ERROR: could not fetch release metadata for ${TAG} from ${api_url}" >&2
    exit 1
  }
  while IFS=$'\t' read -r asset_name asset_url; do
    [[ -z "${asset_name}" ]] && continue
    echo "    Downloading ${asset_name}..."
    curl -fsSL -o "${asset_name}" "${asset_url}"
  done < <(python3 -c "
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
  echo "ERROR: neither 'gh' nor 'curl' found, and no assets already present in" >&2
  echo "       ${WORK_DIR}/assets. Install one of them, or download the release" >&2
  echo "       assets for ${TAG} from https://github.com/${REPO_SLUG}/releases/tag/${TAG}" >&2
  echo "       into ${WORK_DIR}/assets and re-run." >&2
  exit 1
fi

# --- 2. Verify checksums ---------------------------------------------------------
echo "==> Verifying checksums.txt"
sha256_cmd -c checksums.txt
echo "    Checksums OK."

# --- 3. Verify cosign signatures --------------------------------------------------
if ! command -v cosign &>/dev/null; then
  echo "ERROR: 'cosign' not found on PATH. Install from https://docs.sigstore.dev/cosign/installation/" >&2
  exit 1
fi

version="${TAG#v}"
manifest_json="component-manifest.json"
if [[ ! -f "${manifest_json}" ]]; then
  echo "ERROR: ${manifest_json} not found among downloaded assets" >&2
  exit 1
fi

for image in project-code superset; do
  reference="$(python3 -c "
import json
with open('${manifest_json}') as f:
    manifest = json.load(f)
print(manifest['resolved_images']['${image}'])
")"
  echo "==> Verifying cosign signature for ${reference}"
  cosign verify \
    --certificate-identity-regexp "^https://github.com/${REPO_SLUG}/.github/workflows/release.yml@refs/tags/${TAG}\$" \
    --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
    "${reference}"
done
echo "    Signatures OK."

# --- 4. Clean-checkout structural verification ------------------------------------
echo "==> Cloning ${REPO_SLUG}@${TAG} into a clean checkout"
clone_dir="${WORK_DIR}/checkout"
git clone --depth 1 --branch "${TAG}" "https://github.com/${REPO_SLUG}.git" "${clone_dir}"

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
    reference="$(python3 -c "
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
echo "OpenLakeForge ${TAG} verified: checksums OK, signatures OK, clean checkout OK."
