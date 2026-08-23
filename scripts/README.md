# Scripts

Repository validation scripts, and the shell helpers the remaining
non-lifecycle artifact tooling still depends on, live here.

Repository validation scripts live under `scripts/test/`.
`check-structure.sh` validates the repository skeleton and documentation
contract. `check-infra.sh` runs Terraform formatting/validation and renders the
upstream Helm charts with local values.
Provider contract validation — the boundary, logical product aliases, and
generated runtime profile expectations — is behavioral and lives in
`tools/olf/olf/contracts_check.py` (`olf contracts check`), wired through
`make check-contracts`; see [ADR 0017](../docs/adr/0017-shared-python-deploy-tooling.md).
## Shell vs. Python boundary

Every provider's deployment lifecycle now runs entirely in Python (see
below); the shell that remains here wraps the standalone, non-lifecycle
artifact tooling - Floe manifest generation, dbt profile rendering - that
still shells out to `docker`/`dbt` directly. Cross-environment logic that
is not a CLI call — REST/API requests, object-storage uploads, report
bundle manipulation, credential handling, and provider-contract parsing —
lives in the uv-managed Python package `tools/olf`, exposed through the
`olf` CLI. Shell reaches it through `scripts/lib/python.sh` (`olf_run`),
which runs `uv run --project tools/olf`. See
[ADR 0017](../docs/adr/0017-shared-python-deploy-tooling.md).
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
- `scripts/artifacts/floe-manifest.sh` — generates manifest-first product Floe
  contracts from the shared profile in `libs/floe/profiles/`.
- `scripts/artifacts/dbt-parse.sh` — renders product dbt profiles from
  `libs/dbt/profiles/` before parsing.
- `scripts/artifacts/olf.sh` — loads the contract environment, then runs an
  `olf` subcommand (used by the standalone artifact Make targets).

`check-project-code.sh` installs project-code dependencies into a local cache and
verifies that the domain Dagster definitions load.

Every provider's deployment lifecycle - foundation, image prefetch/build/
push, static platform apply, dynamic artifact deploy, status, port-forward,
and teardown - is orchestrated by the Python deployment engine under
`tools/olf/olf/deployment/{engine.py,local/,cloud/}`, exposed through
`olf deploy|destroy|status|forward --provider local|aws|azure`; see
[ADR 0025](../docs/adr/0025-olf-owns-local-deployment-orchestration.md) for
local and [ADR 0027](../docs/adr/0027-olf-owns-cloud-deployment-orchestration.md)
for AWS/Azure. The `local-*`/`azure-*`/`aws-*` `make` targets (`*-up`,
`*-down`, `*-foundation-*`, `*-platform-*`, `*-artifacts-deploy`,
`*-status`, `*-forward`, `*-e2e`) are thin delegates to that engine.
`scripts/azure/` and `scripts/aws/` no longer exist. `scripts/local/` still
holds the standalone image build/load helpers used by the
`project-code-image`/`project-code-load`/`superset-image`/`superset-load`
Make targets:

- `images/` contains local image build/load helpers for project-code and
  Superset.

Manifest upload, Superset report deploy/export, and OpenMetadata metadata
deploy are `olf` subcommands (`artifacts upload-manifests`,
`superset deploy-reports` / `export-reports`, `openmetadata deploy-metadata`);
the standalone (non-lifecycle) Make targets for these still reach them
through `scripts/artifacts/olf.sh`. The full deploy/artifacts lifecycle for
every provider calls the same subcommands directly, in-process, through
`olf.deployment.artifact_steps`.

The Makefile is the public interface for normal use.
