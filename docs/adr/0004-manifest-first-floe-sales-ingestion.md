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
code, and the generated Sales Floe manifest used to load the Dagster asset
graph. It does not install the Floe CLI. Local developer workflows run
`floe manifest generate` before building the image. Local/CD artifact upload
publishes the same generated Sales manifest to the SeaweedFS code bucket for the
separate Kubernetes runner using `ghcr.io/malon64/floe:0.4.6`.

Polaris owns separate service principals for Trino and Floe. Floe credentials
are stored in `polaris-floe-creds`.

The Iteration 3 Dagster milestone materialized Bronze and Silver Sales assets.
Iteration 4 folds that path into the durable `sales_etl_pipeline` job, which
developers launch from the Dagster UI after deploying the stack and forwarding
the webserver.

## Consequences

- Dagster parses a generated Floe manifest instead of Floe YAML at runtime.
- Dagster loads the Sales Floe asset graph from the manifest baked into the
  project-code image.
- The separate Floe runner expects the same manifest at
  `s3://openlakeforge-code/floe/sales/sales.manifest.json`.
- Floe execution is isolated in Kubernetes jobs from the Floe runner image.
- Artifact publication is a CD concern and is not modeled as a Terraform
  resource.
- dbt and DuckDB remain outside Iteration 3.
