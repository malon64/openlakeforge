# Scripts

Local developer and repository validation scripts live here.

Repository validation scripts live under `scripts/test/`.
`check-structure.sh` validates the repository skeleton and documentation
contract. `check-infra.sh` runs Terraform formatting/validation and renders the
upstream Helm charts with local values.

`check-project-code.sh` installs project-code dependencies into a local cache and
verifies that the Sales Dagster pipeline definitions load.
`scripts/local/artifacts/floe-manifest.sh` generates the manifest-first Sales Floe
contract.

Local stack scripts under `scripts/local/` are grouped by lifecycle:

- `stack/` contains the usual local orchestration entrypoints: infra up,
  dynamic artifact deploy, full setup, and teardown.
- `cluster/` contains kind lifecycle and image prefetch helpers.
- `images/` contains local image build/load helpers for project-code and
  Superset.
- `artifacts/` contains local/CD-style domain artifact helpers: Floe manifest
  generation/upload, dbt parse, Superset report deploy/export, and
  OpenMetadata metadata deploy.

The Makefile is the public interface for normal use. The shell scripts stay
focused implementation details behind those targets.
