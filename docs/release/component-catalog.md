# Component catalog

`release/component-catalog.yaml` is the release input inventory. It records the
distribution version, Terraform providers, Helm charts, Python lockfiles,
container digests, and GitHub Action commits used by a release.

Release tags are create-only semantic-version tags. Development tags may move,
but a release tag must never be force-updated. A dependency update changes the
catalog and its lockfile in the same review, then runs `make check-components`,
all repository checks, and both runtime image builds. The catalog is included
unchanged in the release artifact and is the authoritative input manifest.

See [releasing.md](releasing.md) for the full release pipeline — how
`.github/workflows/release.yml` turns this catalog into a signed, SBOM'd,
provenance-attested release bundle — and [compatibility-matrix.md](compatibility-matrix.md)
for the matrix rendered from it. `make release-check` (backed by
`olf release check`) is the release-readiness gate: it fails if the tag
doesn't match `distribution.version`, if any catalog image is missing its
`@sha256` digest, if any workflow action isn't SHA-pinned and recorded under
`components.actions`, or if a lockfile is missing.
