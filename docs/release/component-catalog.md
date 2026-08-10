# Component catalog

`release/component-catalog.yaml` is the release input inventory. It records the
distribution version, tracked Terraform providers, Python lockfiles,
container digests, and GitHub Action commits used by a release.

Release tags are create-only semantic-version tags. Development tags may move,
but a release tag must never be force-updated. A dependency update changes the
catalog and its lockfile in the same review, then runs `make check-components`,
all repository checks, and both runtime image builds. The catalog is included
unchanged in the release artifact and is the authoritative input manifest.

See [releasing.md](releasing.md) for the full release pipeline — how
`.github/workflows/release.yml` turns this catalog into a signed, SBOM'd,
provenance-attested release bundle — and [compatibility-matrix.md](compatibility-matrix.md)
for the matrix rendered from it. `make release-check` is the release-readiness
gate. It runs `olf release check` (fails if the tag doesn't match
`distribution.version`, if any catalog image is missing its `@sha256` digest,
if any workflow action isn't SHA-pinned and recorded under
`components.actions`, if an action catalog entry is unused, or if a Dockerfile
base image is unpinned) and `scripts/test/check-lockfiles.sh` (reads every
lockfile declared in `components.python` and fails if one is out of sync with
its sibling `pyproject.toml`, checked with `uv` directly rather than a
duplicate implementation here).

The gate also rejects a catalog Terraform requirement that differs from any
Terraform root's own `terraform.required_version`, so the compatibility matrix
cannot advertise a broader or narrower Terraform version range than the code
actually accepts.

The compatibility matrix's per-root Terraform provider table and Helm charts
table are both read directly from their real source at render time — the
former from each `.terraform.lock.hcl` under `infra/terraform`, the latter
from each Terraform module's own `chart_version` variable default under
`infra/terraform/modules` — rather than from a cataloged copy, so neither can
drift from what actually gets deployed.
