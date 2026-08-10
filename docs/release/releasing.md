# Releasing OpenLakeForge

A tagged OpenLakeForge release must be independently verifiable and
installable from a clean checkout. This document covers both sides of that
contract: how a maintainer cuts a release, and how a consumer verifies one.

See also [component-catalog.md](component-catalog.md) for how the input
inventory is maintained, and [compatibility-matrix.md](compatibility-matrix.md)
for the published compatibility matrix.

## Pipeline shape

`.github/workflows/release.yml` runs on two triggers:

| Trigger | Behavior |
| --- | --- |
| `push: tags: ['v*']` | Full publish: builds, pushes, signs, attests, and creates the GitHub Release. |
| `workflow_dispatch` with `dry_run: true` (default) | Rehearses the entire pipeline without pushing images, creating the tag, or publishing the Release. Every would-be asset (manifest, compatibility matrix, SBOMs, checksums) is uploaded as a workflow artifact instead. |
| `workflow_dispatch` with `dry_run: false` | Allowed **only when the selected ref is a tag**; the workflow fails fast otherwise. Keyless cosign derives its certificate identity from the running ref, so publishing from a branch would stamp `release.yml@refs/heads/<branch>` while consumers verify `release.yml@refs/tags/<tag>` — producing a release that fails its own documented signature verification. Prefer pushing the tag. |

Jobs:

1. **`prepare`** — resolves the release version and tag from
   `release/component-catalog.yaml`'s `distribution.version`, runs
   `olf release check` (the release-readiness gate — see below), and (for
   real, non-dry-run publishes) verifies no GitHub Release already exists
   for that tag. Release tags are **create-only** (`release_tag_policy:
   immutable-semver`): a tag is never republished once its Release exists.
2. **`build`** (matrix: `project-code`, `superset`) — builds each image with
   Buildx, pushes it **by digest** (`push-by-digest=true`) and then tags the
   pushed digest with the release version via `docker buildx imagetools
   create`, so the published reference is always traceable back to an
   immutable digest rather than a movable tag. Generates an SPDX SBOM with
   `anchore/sbom-action` (syft), signs the digest keylessly with
   `cosign sign` (Sigstore OIDC — no long-lived keys), attaches the SBOM as
   a cosign attestation (`cosign attest --type spdxjson`), and attaches SLSA
   build provenance with `actions/attest-build-provenance`.
3. **`bundle`** — assembles the release bundle: the component manifest
   (`olf release manifest`, catalog + resolved image digests + git SHA), the
   compatibility matrix (`olf release compatibility-matrix`), a verbatim
   copy of `release/component-catalog.yaml`, `CHANGELOG.md`, both SBOMs, and
   a deterministic `checksums.txt` (`olf release checksums`) covering every
   asset. On a real publish this becomes the GitHub Release; in dry-run mode
   it is uploaded as a single workflow artifact.

The heavy cross-environment logic (manifest construction, checksums,
compatibility-matrix rendering, the readiness gate) lives in
`tools/olf/olf/release.py` behind the `olf release` CLI, unit-tested in
`tools/olf/tests/test_release.py`. The workflow and
`scripts/release/build-bundle.sh`/`scripts/release/verify-install.sh` remain
thin shell orchestrators over docker/cosign/syft/git, per
[ADR 0017](../adr/0017-shared-python-deploy-tooling.md).

## Cutting a release

1. Update `release/component-catalog.yaml`'s `distribution.version` (and any
   changed component versions/digests/lockfiles) in the same PR as the code
   it describes. Run `make release-check` locally.
2. Add a `## [<version>]` entry to `CHANGELOG.md` with migration notes,
   replacing "Unreleased" with the release date.
3. Merge to `main`. Confirm the `release-check` job in
   `.github/workflows/checks.yml` is green on `main`.
4. **Rehearse first**: trigger `.github/workflows/release.yml` via
   `workflow_dispatch` with `dry_run: true` (the default). Inspect the
   uploaded `release-dry-run-bundle-<version>` artifact — it should contain
   `component-manifest.json`, `compatibility-matrix.md`,
   `component-catalog.yaml`, `CHANGELOG.md`, both SBOMs, and
   `checksums.txt`.
5. Once the dry run looks right, cut the real tag:
   `git tag v<version> && git push origin v<version>`. The tag push triggers
   the same workflow with `dry_run: false`, which builds, pushes, signs,
   attests, and publishes the GitHub Release.
6. Run `scripts/release/verify-install.sh v<version>` (see below) to confirm
   the published release is independently verifiable and installs from a
   clean checkout.

`make release-check` fails a release if:

- `distribution.version` isn't a valid alpha semver, or (when `--tag` is
  passed) the tag doesn't match it.
- Any catalog image under `components.images` is missing an `@sha256:...`
  digest, or (for images with a Helm deployment source) doesn't match the
  digest actually referenced under `infra/helm/values`.
- Any `.github/workflows/*.yml` `uses:` line isn't pinned to a 40-character
  commit SHA, or isn't recorded in `components.actions`.
- Any `images/*/Dockerfile` has an unpinned `FROM`/`ARG *_IMAGE=` outside of
  `FROM ${...}` build-arg indirection.
- `docs/release/compatibility-matrix.md` doesn't match a fresh render of the
  catalog.
- Either Python lockfile (`images/project-code/requirements.lock`,
  `tools/olf/uv.lock`) is out of sync with its `pyproject.toml` --
  checked with `uv lock --check` and a seeded `uv pip compile` re-run
  (`scripts/test/check-lockfiles.sh`), not a hand-rolled parser.

## Consumer verification

Every release publishes:

- Two signed container images: `ghcr.io/malon64/openlakeforge/project-code`
  and `ghcr.io/malon64/openlakeforge/superset`, tagged with the release
  version and referenced by digest.
- `component-manifest.json` — the component catalog plus the resolved image
  digests and the git commit the release was built from.
- `compatibility-matrix.md` — OpenLakeForge ↔ Kubernetes ↔ Terraform ↔ Helm
  chart ↔ cloud service compatibility, sourced from the catalog.
- `component-catalog.yaml` — the exact input manifest, copied verbatim.
- `project-code.spdx.json` / `superset.spdx.json` — SPDX SBOMs, both
  attached to the Release and available as cosign attestations on the image
  digests.
- `checksums.txt` — sha256 over every asset above.
- `CHANGELOG.md`.

### Verify signatures

```sh
cosign verify \
  --certificate-identity-regexp '^https://github\.com/malon64/openlakeforge/\.github/workflows/release\.yml@refs/tags/v0\.1\.0-alpha\.1$' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ghcr.io/malon64/openlakeforge/project-code@sha256:<digest>
```

Repeat for the `superset` image. The digest comes from
`component-manifest.json`'s `resolved_images`, so verification never trusts
a mutable tag.

### Verify the SBOM attestation

```sh
cosign verify-attestation \
  --type spdxjson \
  --certificate-identity-regexp '^https://github\.com/malon64/openlakeforge/\.github/workflows/release\.yml@refs/tags/v0\.1\.0-alpha\.1$' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ghcr.io/malon64/openlakeforge/project-code@sha256:<digest>
```

### Verify checksums

```sh
sha256sum -c checksums.txt
```

### Verify a clean-checkout install

```sh
scripts/release/verify-install.sh v0.1.0-alpha.1
```

This script downloads the release assets with `gh release download`,
verifies `checksums.txt`, verifies the cosign signature on both published
image digests, then does a fresh `git clone --branch v0.1.0-alpha.1` into a
scratch directory and runs `scripts/test/check-structure.sh` and
`scripts/test/check-components.sh` from that clean checkout — proving the
tagged tree is self-consistent with no local state carried over. Pass
`--pull-images` to additionally `docker pull` both published images by
digest.

This script requires `git`, `gh`, `cosign`, `uv`, `docker` (only for
`--pull-images`), and either `sha256sum` or `shasum`. It is safe to run by
anyone: it only reads public release assets and verifies public Sigstore
signatures.

## What ran where

- `make release-check`, `make check-components`, `ruff check tools/olf`, and
  `pytest tools/olf/tests` were run locally as part of this change; see the
  PR description for results.
- The `release.yml` workflow itself — including a real `workflow_dispatch`
  dry run — was **not** executed as part of authoring this change, because
  this environment cannot trigger GitHub Actions runs. `scripts/release/
  verify-install.sh` was written and syntax-checked, but not run end-to-end
  against a published release, because no tag has been published yet. The
  maintainer should trigger `workflow_dispatch` with `dry_run: true` after
  merging, inspect the uploaded artifact, and only then cut the real
  `v0.1.0-alpha.1` tag.
