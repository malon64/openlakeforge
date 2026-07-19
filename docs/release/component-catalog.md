# Component catalog

`release/component-catalog.yaml` is the release input inventory. It records the
distribution version, Terraform providers, Helm charts, Python lockfiles,
container digests, and GitHub Action commits used by a release.

Release tags are create-only semantic-version tags. Development tags may move,
but a release tag must never be force-updated. A dependency update changes the
catalog and its lockfile in the same review, then runs `make check-components`,
all repository checks, and both runtime image builds. The catalog is included
unchanged in the release artifact and is the authoritative input manifest.
