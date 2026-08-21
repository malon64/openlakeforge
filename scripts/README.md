# Scripts

Local, Azure POC, AWS POC, and repository validation scripts live here.

Repository validation scripts live under `scripts/test/`.
`check-structure.sh` validates the repository skeleton and documentation
contract. `check-infra.sh` runs Terraform formatting/validation and renders the
upstream Helm charts with local values.
Provider contract validation — the boundary, logical product aliases, and
generated runtime profile expectations — is behavioral and lives in
`tools/olf/olf/contracts_check.py` (`olf contracts check`), wired through
`make check-contracts`; see [ADR 0017](../docs/adr/0017-shared-python-deploy-tooling.md).
## Shell vs. Python boundary

Shell scripts orchestrate the CLIs (terraform, kubectl, helm, docker, dbt,
floe, aws, az). Cross-environment logic that is not a CLI call — REST/API
requests, object-storage uploads, report bundle manipulation, credential
handling, and provider-contract parsing — lives in the uv-managed Python
package `tools/olf`, exposed through the `olf` CLI. Shell reaches it through
`scripts/lib/python.sh` (`olf_run`), which runs `uv run --project tools/olf`.
See [ADR 0017](../docs/adr/0017-shared-python-deploy-tooling.md).
End-to-end validation also lives in `olf` (`olf e2e run`); the public Make
targets call it directly with environment-specific defaults.

Shared shell helpers live under `scripts/lib/`:

- `common.sh` — `require_cmd`, `check_prereqs`, `run_with_retry`, tag helpers.
- `helm.sh` — Helm chart cache download/reuse.
- `kube.sh` — kubectl helpers: secret reads, rollout/restart, failed-job
  cleanup, Dagster code-location discovery, and the Polaris bootstrap preflight.
- `python.sh` — `olf_run` entrypoint for the `tools/olf` package.
- `docker.sh` — Docker pulls/builds/pushes with retry for transient failures.

Environment-neutral shell lives outside `scripts/local/`:

- `scripts/contracts/load-runtime-env.sh` — sourced by every phase; evaluates
  `olf contracts env` to export the provider-contract runtime environment.
- `scripts/artifacts/floe-manifest.sh` — generates manifest-first domain Floe
  contracts from the shared profile in `libs/floe/profiles/`.
- `scripts/artifacts/dbt-parse.sh` — renders product dbt profiles from
  `libs/dbt/profiles/` before parsing.
- `scripts/artifacts/olf.sh` — loads the contract environment, then runs an
  `olf` subcommand (used by the standalone artifact Make targets).

`check-project-code.sh` installs project-code dependencies into a local cache and
verifies that the domain Dagster definitions load.

The local (kind-based) deployment lifecycle - foundation, image prefetch,
static platform apply, dynamic artifact deploy, status, port-forward, and
teardown - is orchestrated by the Python deployment engine under
`tools/olf/olf/deployment/{engine.py,local/}`, exposed through
`olf deploy|destroy|status|forward --provider local`; see
[ADR 0025](../docs/adr/0025-olf-owns-local-deployment-orchestration.md). The
local `make` targets (`local-up`, `local-slim-up`, `local-down`,
`local-foundation-*`, `local-platform-*`, `local-artifacts-deploy`,
`local-prefetch`, `local-status`, `local-forward`) are thin delegates to that
engine. `scripts/local/` now only holds the standalone image build/load
helpers used by the `project-code-image`/`project-code-load`/
`superset-image`/`superset-load` Make targets:

- `images/` contains local image build/load helpers for project-code and
  Superset.

Manifest upload, Superset report deploy/export, and OpenMetadata metadata
deploy are now `olf` subcommands (`artifacts upload-manifests`,
`superset deploy-reports` / `export-reports`, `openmetadata deploy-metadata`),
invoked by the per-environment `stack/deploy-artifacts.sh` orchestrators.

Azure POC scripts under `scripts/azure/` mirror the local lifecycle without
overloading local behavior:

- `foundation/` contains Terraform wrappers for AKS and ACR.
- `stack/` contains Azure platform up, dynamic artifact deploy, and teardown
  wrappers.
- `images/` builds and pushes Superset and project-code images to ACR.
- e2e validation is exposed directly through `make azure-e2e`, which runs
  `olf e2e run --env azure` against Dagster, Trino, Superset, OpenMetadata,
  and ops artifacts.

AWS POC scripts under `scripts/aws/` mirror Azure while using AWS managed
services:

- `foundation/` contains Terraform wrappers for EKS, ECR, and EKS Pod Identity readiness.
- `stack/` contains AWS platform up, S3/ECR artifact deploy, and teardown
  wrappers.
- `images/` builds and pushes Superset and project-code images to ECR.
  `PROJECT_CODE_PYTHON_BASE_IMAGE` defaults to the ECR Public Docker Library
  mirror for AWS project-code builds to avoid depending on Docker Hub during
  `make aws-artifacts-deploy`.
- e2e validation is exposed directly through `make aws-e2e`, which runs
  `olf e2e run --env aws`: EKS provider, pod, S3, Glue, and Trino preflight
  checks followed by the shared full Dagster and reporting validation.

The Makefile is the public interface for normal use. The shell scripts stay
focused implementation details behind those targets.
