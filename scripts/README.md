# Scripts

Local, Azure POC, AWS POC, and repository validation scripts live here.

Repository validation scripts live under `scripts/test/`.
`check-structure.sh` validates the repository skeleton and documentation
contract. `check-infra.sh` runs Terraform formatting/validation and renders the
upstream Helm charts with local values.
`check-contracts.sh` validates the provider contract boundary, logical product
aliases, and generated runtime profile expectations.
Shared shell helpers live under `scripts/lib/`; `docker.sh` wraps Docker pulls,
builds, and pushes with retry behavior for transient registry/network failures.

`check-project-code.sh` installs project-code dependencies into a local cache and
verifies that the domain Dagster definitions load.
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

AWS POC scripts under `scripts/aws/` mirror Azure while using AWS managed
services:

- `foundation/` contains Terraform wrappers for EKS, ECR, and EKS Pod Identity readiness.
- `stack/` contains AWS infra up, S3/ECR artifact deploy, full setup, and
  teardown wrappers.
- `images/` builds and pushes Superset and project-code images to ECR.
  `PROJECT_CODE_PYTHON_BASE_IMAGE` defaults to the ECR Public Docker Library
  mirror for AWS project-code builds to avoid depending on Docker Hub during
  `make aws-artifacts-deploy`.
- `test/` runs the EKS smoke validation against provider contracts, pods, S3,
  Glue, and Trino.

The Makefile is the public interface for normal use. The shell scripts stay
focused implementation details behind those targets.
