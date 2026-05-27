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

The project-code image contains Dagster code, `dagster-floe`, dlt code, and
domain code. It does not install the Floe CLI and does not bake generated Floe
manifests into the image. Local developer workflows run `floe manifest generate`
before applying the stack. Terraform uploads the generated Sales manifest and
config to the SeaweedFS code bucket, and the manifest profile declares a
Kubernetes runner using `ghcr.io/malon64/floe:0.4.3`.

Polaris owns separate service principals for Trino and Floe. Floe credentials
are stored in `polaris-floe-creds`.

The durable Dagster job for this path is `sales_bronze_to_silver_job`. Local
developers launch it from the Dagster UI after deploying the stack and forwarding
the webserver.

## Consequences

- Dagster parses a generated Floe manifest instead of Floe YAML at runtime.
- The local stack stores the Sales Floe manifest and config in
  `s3://openlakeforge-code/floe/sales/`.
- Floe execution is isolated in Kubernetes jobs from the Floe runner image.
- `make local-up` requires a generated Sales Floe manifest because Terraform
  uploads it to SeaweedFS before Dagster starts.
- dbt and DuckDB remain outside Iteration 3.
