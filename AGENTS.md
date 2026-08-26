# Agent and contributor guide

This is the working guide for anyone changing OpenLakeForge — human or coding
agent. It records what the repository actually enforces today. If a statement
here disagrees with the code, the code wins and this file is a bug.

`CLAUDE.md` points here. There is one copy of these rules on purpose.

## What this project is

OpenLakeForge is a cloud-agnostic, self-hostable lakehouse platform: open-source
components assembled on Kubernetes with Terraform and Helm. The data path is
CSV → Bronze → Floe validation → Silver Iceberg through Polaris → dbt-trino Gold
→ Trino → Superset, orchestrated by Dagster.

The audience is small data teams self-hosting a complete lakehouse. Runtime
footprint, onboarding friction, and recoverability are product features here,
not polish — a team without a platform engineer cannot absorb a stack that needs
one. `v0.2-alpha` is scoped around exactly that; see the roadmap.

## Orientation — read in this order

1. `README.md` — stack, deployment targets, local workflow
2. `docs/industrialization-roadmap.md` — milestones, release gates, what is
   delivered and what is not
3. `docs/architecture/overview.md` and `docs/architecture/provider-contracts.md`
4. `docs/adr/README.md` — the decision log index; ADRs are binding
5. `docs/technical-debt.md` — the live debt register with a fix path per item
6. `docs/architecture/diagrams/README.md` — pod census and runtime topology

Current work is tracked in GitHub milestones. Issues carry `priority: P0/P1/P2`
labels expressing intended sequence within a milestone.

## Repository map

| Path | Contains |
| --- | --- |
| `lakehouse_code/bronze/<source>/` | Source-owned: `source.yaml` descriptor plus dlt extract |
| `lakehouse_code/silver/<domain>/` | Domain-owned: Floe contracts and transformations |
| `lakehouse_code/gold/<product>/` | Product-owned: dbt project |
| `lakehouse_code/dashboards/superset/<dashboard>/` | Consumption-owned: Superset reports |
| `lakehouse_code/pipelines/dagster/` | User-maintained Dagster orchestration code |
| `lakehouse_code/lakehouse.yaml` | Canonical domain/product business metadata descriptor |
| `libs/` | Shared runtime Python imported by the project-code image |
| `packages/domain-model/` | Canonical provider-neutral descriptor and inventory package |
| `tools/olf/` | The `olf` CLI — uv-managed deploy tooling, contracts, artifacts, scaffolding, e2e |
| `infra/terraform/environments/` | Per-environment wiring; `contracts.tf` is the contract surface |
| `infra/terraform/foundations/` | Cluster and registry creation (kind, AKS, EKS) |
| `infra/terraform/modules/` | Component modules grouped by capability |
| `infra/helm/values/local/` | Helm values for the local profile |
| `images/project-code/` | The Dagster runtime image |
| `release/component-catalog.yaml` | Immutable version, digest pins, and the distribution version contract both `pyproject.toml` files must match |
| `docs/schema/lakehouse.schema.json`, `docs/schema/source.schema.json` | Schemas `lakehouse.yaml` and `source.yaml` validate against |

## Architectural rules

These are load-bearing. Breaking one means the change is wrong even if it works.

1. **Contracts before providers.** Components communicate through the provider
   contracts in `contracts.tf`, never directly with a provider. Adding a
   capability means extending the contract, then writing an adapter.
2. **Descriptors stay provider-neutral.** `lakehouse_code/lakehouse.yaml` and
   `lakehouse_code/bronze/<source>/source.yaml` must not name Polaris, Glue, S3,
   or SeaweedFS. Physical catalog and object-store names are derived from these
   logical identities by provider/deployment contracts, never embedded in the
   descriptors themselves — see `docs/schema/lakehouse.schema.json` and
   `docs/schema/source.schema.json`.
3. **Two-phase deploy** (ADR 0008). Phase 1 is the static platform
   (`olf deploy --phase platform`); Phase 2 is dynamic artifacts
   (`olf deploy --phase artifacts`). Static Terraform resources belong to
   Phase 1; anything built from domain code belongs to Phase 2. Never make a
   platform apply wait on an artifact.

   `olf deploy` with no `--phase` runs foundation, then platform, then
   artifacts in sequence (ADR 0028). That is the wrapper sequence, not a
   third deploy phase — the two-phase boundary is unchanged. Foundation work
   (cluster and registry creation) is separate from Phase 1, not part of it.
4. **`olf` owns repository orchestration** (ADR 0028). Cross-environment
   logic lives in `tools/olf` with tests. There is no tracked shell; `olf
   check structure` rejects it. `Makefile` is deprecated checkout
   compatibility only — its targets are one-line delegates to `olf`.
5. **No hardcoded product knowledge in shared code.** Product lists, job names,
   table names, and dashboard names are moving to descriptor-driven discovery.
   Do not add new ones.
6. **Pins are immutable.** Images are pinned by digest and recorded in
   `release/component-catalog.yaml`. Changing a version means updating the
   catalog in the same change; `olf check components` enforces this.
7. **No credentials in generated artifacts or runtime defaults.** Floe manifests
   are generated by Floe and never post-processed. Secrets reach pods through
   Kubernetes Secret references.
8. **ADR for shape changes.** Anything that moves a component boundary, replaces
   a technology, or changes the deploy model needs a new numbered ADR in
   `docs/adr/` plus an entry in `docs/adr/README.md`. ADRs supersede; they are
   not edited after the fact.

## Standards

**Python.** 3.12 — `project-code` requires `>=3.12,<3.13`, `tools/olf` requires
`>=3.12`. Ruff with line length 120 and rules `E,F,W,I,UP,B`; configuration in
`tools/olf/pyproject.toml`. Follow the surrounding style: `from __future__ import
annotations` at module top, type annotations on public functions, frozen
dataclasses for value objects.

**Tests.** pytest under `tools/olf/tests/`, broadly one module per `olf` module.
Test behaviour against fixtures rather than source text. New `olf` behaviour
ships with tests in the same change.

**Terraform.** Modules follow `main.tf` / `variables.tf` / `outputs.tf`;
`olf check structure` requires all three for registered Terraform modules.
Variables carry a `description` and an explicit `type`.

**Docs.** Hard-wrap around 80 columns, as the existing files do. Prefer tables
over prose for anything enumerable. No plain box-and-arrow Mermaid flowcharts —
the architecture charts use the Kubernetes-icon SVG toolkit in
`docs/architecture/diagrams/src/`; sequence diagrams are fine.

**Commits.** `type: subject`, imperative, lowercase after the colon. Types in
use: `feat`, `fix`, `docs`, `refactor`, `chore`. The body explains why.

**Structure.** `olf check structure` holds the repository skeleton contract and
rejects tracked shell scripts. A new file that should always exist must be
registered there.

## Gates — all must pass before opening a pull request

```bash
uv run --project tools/olf olf check all
uv run --project tools/olf ruff check tools/olf
uv run --project tools/olf pytest tools/olf/tests
```

`olf check all` runs the check targets above plus the release-readiness
gate. It runs on every pull request via `.github/workflows/checks.yml`. Note
that `main` is currently unprotected, so no check is merge-blocking yet — see
"Known gaps".

When a change touches the deployed stack, verify at runtime as well:

```bash
uv run --project tools/olf olf deploy --provider local
uv run --project tools/olf olf e2e run --env local
```

`olf e2e run --env local` launches every product pipeline, verifies Silver and Gold
tables through Trino, checks Superset dashboards and OpenMetadata assets, and
confirms ops-bucket artifacts exist.

## Guardrails

- Never commit to `main`. Branch, then open a pull request.
- Never edit generated Floe manifests under
  `lakehouse_code/silver/<domain>/contracts/floe/manifests/`. They are produced
  by `olf floe generate-manifests` and owned by Floe.
- Never unpin an image or chart to make something work.
- Never widen scope beyond the issue. Note adjacent problems in the pull request
  body instead of fixing them silently.
- If a gate fails for a reason unrelated to the change, say so in the pull
  request rather than working around it.
- Ask before inventing facts that belong to the maintainer: security contacts,
  ownership, support commitments, or anything that implies a promise to users.

## Known gaps

Governance artifacts — `CONTRIBUTING`, `SECURITY`, `SUPPORT`, `GOVERNANCE`, and
`CODEOWNERS` — do not exist yet, and `main` is currently unprotected with no
dependency-update automation. Tracked in
[#37](https://github.com/malon64/openlakeforge/issues/37). Until they land, this
file is the contribution guide.
