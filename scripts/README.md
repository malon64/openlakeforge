# Scripts

Local, Azure POC, and repository validation scripts live here.

Repository validation scripts live under `scripts/test/`.
`check-structure.sh` validates the repository skeleton and documentation
contract. `check-infra.sh` runs Terraform formatting/validation and renders the
upstream Helm charts with local values.
`check-contracts.sh` validates the provider contract boundary, logical product
aliases, and generated runtime profile expectations.

`check-project-code.sh` installs project-code dependencies into a local cache and
verifies that the aggregate product Dagster definitions load.
`scripts/local/artifacts/floe-manifest.sh` generates manifest-first product Floe
contracts from the shared profile in `libs/floe/profiles/`.

Local stack scripts under `scripts/local/` are grouped by lifecycle:

- `stack/` contains the usual local orchestration entrypoints: infra up,
  dynamic artifact deploy, full setup, and teardown.
- `foundation/` contains Terraform wrappers for the local kind foundation.
- `contracts/` contains helpers that load Terraform provider contracts and
  render local runtime profiles.
- `cluster/` contains kind image prefetch helpers.
- `images/` contains local image build/load helpers for project-code and
  Superset.
- `artifacts/` contains local/CD-style domain artifact helpers: Floe manifest
  generation/upload, dbt parse, Superset report deploy/export, and
  OpenMetadata metadata deploy.

Azure POC scripts under `scripts/azure/` mirror the local lifecycle without
overloading local behavior:

- `foundation/` contains Terraform wrappers for AKS and ACR.
- `stack/` contains Azure infra up, dynamic artifact deploy, full setup, and
  teardown wrappers.
- `images/` builds and pushes Superset and project-code images to ACR.
- `test/` runs the AKS e2e validation against Dagster, Trino, Superset, and
  OpenMetadata.

The Makefile is the public interface for normal use. The shell scripts stay
focused implementation details behind those targets.
