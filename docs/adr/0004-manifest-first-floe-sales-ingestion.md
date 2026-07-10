# ADR 0004: Manifest-First Floe Sales Ingestion

## Status

Accepted

Current implementation note: this Sales-first decision has been generalized to
domain-owned product Floe contracts and manifests under
`domains/<domain>/contracts/floe/`, generated with the shared infrastructure
profile at `libs/floe/profiles/local-k8s.yml`.

## Context

Iteration 3 needs to prove that a domain-owned ingestion path can land Bronze
data and materialize Silver Iceberg tables through Polaris. Floe already exposes
a `floe.manifest.v1` contract for Dagster, and the connector can launch Floe
Kubernetes jobs from the manifest runner definition.

## Decision

Sales owns raw examples, dlt extract code, Floe contracts, generated Floe
manifests, and Dagster definitions under `domains/sales`.

The project-code image contains Dagster code, `dagster-floe`, dlt code, domain
code, and generated product Floe manifests used to load the Dagster asset graph.
It does not install the Floe CLI. Local developer workflows run
`floe manifest generate` before building the image so Dagster definitions load
from the manifest baked into the project-code image.

Kubernetes Floe execution uses explicit remote manifest access:
`OPENLAKEFORGE_FLOE_MANIFEST_ACCESS_MODE=remote`. Local/CD artifact upload
publishes the same generated product manifests to the SeaweedFS ops bucket, and
Dagster passes those `s3://...` manifest URIs to the separate
`ghcr.io/malon64/floe:0.6.8` runner pods. `local` manifest access is reserved
for same-container local-process execution because the separate runner image
cannot read the project-code image filesystem.

Polaris owns separate service principals for Trino and Floe. Floe credentials
are stored in `polaris-floe-creds`.

The Iteration 3 Dagster milestone materialized Bronze and Silver Sales assets.
The current implementation generalizes that path into durable product jobs such
as `sales_order_revenue_pipeline`, `sales_customer_health_pipeline`, and
`supply_chain_inventory_reliability_pipeline`.

## Consequences

- Dagster parses a generated Floe manifest instead of Floe YAML at runtime.
- Dagster loads product Floe asset graphs from local manifests baked into the
  project-code image.
- The separate Floe runner expects the same product manifests under
  `s3://openlakeforge-ops/floe/manifests/<domain>/<product>/<product>.manifest.json`.
- Remote manifest mode fails fast if a product URI cannot be resolved.
- Floe execution is isolated in Kubernetes jobs from the Floe runner image.
- Artifact publication is a CD concern and is not modeled as a Terraform
  resource.
- dbt and DuckDB remain outside Iteration 3.
