# ADR 0004: Manifest-First Floe Sales Ingestion

## Status

Accepted

## Context

Iteration 3 needs to prove that a domain-owned ingestion path can land Bronze
data and materialize Silver Iceberg tables through Polaris. Floe already exposes
a `floe.manifest.v1` contract for Dagster, and the connector can launch Floe
Kubernetes jobs from the manifest runner definition.

## Decision

Sales owns raw examples, dlt extract code, Floe contracts, generated Floe
manifests, and Dagster definitions under `domains/sales`.

The project-code image contains Dagster code, `dagster-floe`, dlt code, domain
code, and the generated manifest. It does not install the Floe CLI. Local
developer workflows run `floe manifest generate` before building the image. The
manifest profile declares a Kubernetes runner using `ghcr.io/malon64/floe:0.4.2`.

Polaris owns separate service principals for Trino and Floe. Floe credentials
are stored in `polaris-floe-creds`.

## Consequences

- Dagster parses a generated Floe manifest instead of Floe YAML at runtime.
- Floe execution is isolated in Kubernetes jobs from the Floe runner image.
- `make project-code-image` depends on manifest generation unless explicitly
  bypassed with `SKIP_FLOE_MANIFEST=1`.
- dbt and DuckDB remain outside Iteration 3.
