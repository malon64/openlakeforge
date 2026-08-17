#!/usr/bin/env bash
# Install verification for a published OpenLakeForge release.
#
# Run this against a tag that has already been published by
# .github/workflows/release.yml (i.e. a real, non-dry-run release). It
# proves the acceptance-criteria claims in docs/release/releasing.md:
#
#   1. Signatures verify:  cosign verify against the published image digests.
#   2. Checksum manifest:  keyless cosign verification authenticates the
#                          manifest before sha256sum -c checks the assets.
#   3. Clean checkout:     a fresh `git clone` at the release tag passes the
#                          repository's own structural/component consistency
#                          checks with no local state carried over.
#   4. Consumer install (--consumer-install --kube-context <ctx>, opt-in):
#                          `olf install run`/`olf install verify` apply the
#                          tag into an existing cluster with no repository
#                          clone and prove the running image digests match
#                          component-manifest.json exactly (#80).
#
# Usage:
#   scripts/release/verify-install.sh [TAG] [--pull-images]
#     [--consumer-install --kube-context CONTEXT [--profile slim|full] [--strict]]
#
# --strict also fails on any running image the manifest doesn't declare at
# all; it is opt-in because the catalog does not yet register every image a
# `full`-profile install runs (see docs/adr/0023). Omit it for the
# documented default full-profile walkthrough.
#
# TAG defaults to v<distribution.version> from release/component-catalog.yaml
# in the current checkout. Requires: git, curl or gh, cosign, sha256sum (or
# shasum on macOS), uv (https://docs.astral.sh/uv/, used by the cloned
# checkout's own scripts/test/check-components.sh), and docker (for the
# optional --pull-images smoke). --consumer-install additionally requires
# kubectl, terraform, and helm, and CONTEXT must already exist in the
# resolved kubeconfig ($KUBECONFIG, or ~/.kube/config).
#
# This script is intentionally runnable by anyone outside CI: it downloads
# public release assets and verifies public Sigstore signatures, no
# credentials required (--consumer-install additionally needs cluster access).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
REPO_SLUG="${OPENLAKEFORGE_REPO_SLUG:-malon64/openlakeforge}"
WORK_DIR="${OPENLAKEFORGE_VERIFY_WORKDIR:-$(mktemp -d /tmp/openlakeforge-verify.XXXXXX)}"
PULL_IMAGES="false"
CONSUMER_INSTALL="false"
CONSUMER_KUBE_CONTEXT=""
CONSUMER_PROFILE="full"
CONSUMER_STRICT="false"

run_python() {
  uv run --project "${REPO_ROOT}/tools/olf" --locked python "$@"
}

run_olf() {
  uv run --project "${REPO_ROOT}/tools/olf" --locked olf "$@"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --pull-images)
      PULL_IMAGES="true"
      shift
      ;;
    --consumer-install)
      CONSUMER_INSTALL="true"
      shift
      ;;
    --kube-context)
      CONSUMER_KUBE_CONTEXT="${2:-}"
      [[ -n "${CONSUMER_KUBE_CONTEXT}" ]] || { echo "ERROR: --kube-context requires a value" >&2; exit 1; }
      shift 2
      ;;
    --profile)
      CONSUMER_PROFILE="${2:-}"
      [[ -n "${CONSUMER_PROFILE}" ]] || { echo "ERROR: --profile requires a value" >&2; exit 1; }
      shift 2
      ;;
    --strict)
      CONSUMER_STRICT="true"
      shift
      ;;
    -*)
      echo "ERROR: unknown flag $1" >&2
      exit 1
      ;;
    *)
      TAG="$1"
      shift
      ;;
  esac
done

if [[ "${CONSUMER_INSTALL}" == "true" && -z "${CONSUMER_KUBE_CONTEXT}" ]]; then
  echo "ERROR: --consumer-install requires --kube-context <context>" >&2
  exit 1
fi

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

# --- 6. Optional: the consumer install path (#80) ---------------------------------
#
# Everything above proves the release *artifacts* are authentic and the
# tagged *source tree* is self-consistent. It does not prove a consumer can
# actually run the release without cloning it. This step does: it drives
# `olf install` against the already-downloaded, already-verified assets in
# ${WORK_DIR}/assets, then proves the resulting cluster's running image
# digests match component-manifest.json exactly.
if [[ "${CONSUMER_INSTALL}" == "true" ]]; then
  echo "==> Installing ${TAG} (${CONSUMER_PROFILE} profile) into context ${CONSUMER_KUBE_CONTEXT} via 'olf install'"
  run_olf install run \
    --tag "${TAG}" \
    --kube-context "${CONSUMER_KUBE_CONTEXT}" \
    --profile "${CONSUMER_PROFILE}" \
    --assets-dir "${WORK_DIR}/assets"

  echo "==> Proving installed image digests match component-manifest.json"
  verify_args=(
    install verify
    --manifest "${manifest_json}"
    --kube-context "${CONSUMER_KUBE_CONTEXT}"
    --profile "${CONSUMER_PROFILE}"
  )
  # --strict also fails on running images the manifest doesn't declare at
  # all. release/component-catalog.yaml does not yet register every image a
  # `full`-profile install runs (e.g. the OpenMetadata server itself, only
  # its ingestion image is tracked), so --strict is opt-in here rather than
  # the default -- unconditionally enabling it would fail this check against
  # a healthy full-profile install. The non-strict check (every declared
  # image present and at the right digest) still runs unconditionally.
  if [[ "${CONSUMER_STRICT}" == "true" ]]; then
    verify_args+=(--strict)
  fi
  run_olf "${verify_args[@]}"
  echo "    Consumer install OK: running digests match the release manifest exactly."
fi

echo ""
echo "OpenLakeForge ${TAG} verified: authenticated checksums OK, signatures OK, clean checkout OK."
