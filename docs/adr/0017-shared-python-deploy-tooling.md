# ADR 0017: Shared Python deploy tooling with shell CLI orchestration

## Status

Accepted. Amends the wrapper semantics of ADR 0008 without changing its
static platform / dynamic artifacts boundary.

## Context

Deployment automation was ~6,900 lines of shell across `scripts/local`,
`scripts/azure`, and `scripts/aws`. Environment-agnostic logic was duplicated
three times, and several concerns were embedded as Python heredocs inside bash:

- Provider-contract resolution (`load-runtime-env.sh` env-var defaulting plus
  `emit-contract-env.py` and `render-floe-profile.py`).
- OpenMetadata REST seeding (a 669-line bash wrapper around embedded Python).
- Superset report bundle build/import/export.
- Object-storage manifest uploads (SeaweedFS via port-forward, and direct S3).
- Kubernetes project-code image bookkeeping (three duplicated heredoc patch
  functions in the Azure and AWS artifact deploy scripts).
- The Polaris OAuth credential preflight (duplicated curl blocks).

`scripts/local/*` was also acting as shared code — the Azure and AWS deploy
scripts called into `scripts/local/artifacts/*` and
`scripts/local/contracts/*`.

## Decision

Split responsibilities by strength of each tool:

- **Shell stays the orchestrator for CLIs**: terraform, kubectl, helm, docker,
  dbt, floe, aws, and az invocations remain in shell. Repeated shell helpers
  live in `scripts/lib/{common,helm,kube,python,docker}.sh`.
- **Python owns cross-environment logic**: REST/API calls, object-storage
  uploads, zip/bundle manipulation, credential handling, and contract parsing
  move into one uv-managed package, `tools/olf`, exposed through the `olf` CLI
  (`contracts`, `floe`, `artifacts`, `superset`, `openmetadata`, `k8s`,
  `polaris`). Shell calls `olf` through `scripts/lib/python.sh` (`olf_run`).
- **Terraform remains the contract source of truth.** `olf contracts env`
  reads `terraform output -json provider_contracts` and prints `export`/`unset`
  lines that `scripts/contracts/load-runtime-env.sh` evaluates; exported
  variable names are unchanged, so every consumer keeps working.
- **Environment-neutral shell moves out of `scripts/local`.** Shared artifact
  helpers now live under `scripts/artifacts/` and `scripts/contracts/`.
- **`make <env>-up` is a full wrapper**: foundation, then platform, then artifacts.
  Terraform makes the foundation apply a no-op when the cluster already exists.
  The granular targets are `<env>-foundation-up`, `<env>-platform-up`, and
  `<env>-artifacts-deploy`; local also runs `local-prefetch` between foundation
  and platform.

The host gains one prerequisite: `uv` (https://docs.astral.sh/uv/).

## Consequences

- One implementation per concern; the three environments differ only in the
  thin orchestrator scripts and their Terraform roots.
- New capabilities get a natural home: adding Keycloak/Vault/Spark is a new
  Terraform module plus contract fields plus (if it needs API/file work) an
  `olf` subcommand — the deploy shell does not fan out again.
- `olf` logic is unit-tested (`tools/olf/tests`) and linted (ruff) in CI, which
  the previous heredocs could not be.
- Runtime e2e validation is now owned by `olf e2e run`; shell remains as thin
  Make/script wrappers around the shared Python implementation. Broader
  `check-*` migration remains tracked in `docs/technical-debt.md`.
