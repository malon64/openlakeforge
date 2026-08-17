# ADR 0023: Native OpenLineage Emission Restored

## Status

Accepted

## Context

ADR 0009 removed OpenLineage after the proxy-based integration hid upstream
correctness problems. That decision was correct for the dependencies available
then: Floe omitted usable dataset context, and dbt-duckdb produced dataset
namespaces that OpenMetadata could not resolve to the Iceberg catalog.

Those conditions no longer describe the runtime. Floe 0.6.11 emits manifest
entities to its lineage observer, including resolved Bronze source URIs and
Silver Iceberg outputs. OpenLakeForge now uses dbt-trino for Gold, whose
OpenLineage datasets use the Trino namespace rather than a local DuckDB file.
Both engines can send directly to OpenMetadata's native
`/api/v1/openlineage/lineage` endpoint.

The restored path must not repeat ADR 0009's two rejected designs:

- There is no normalising proxy between an engine and OpenMetadata.
- There is no OpenLakeForge-specific Dagster task that upserts lineage edges.

The OpenMetadata Dagster connector remains deferred. Its ingestion query still
uses the obsolete `PipelineRuns` GraphQL type against Dagster 1.x, which is
unrelated to engine-level OpenLineage emission.

## Decision

Enable native OpenLineage emission for Floe and dbt-trino when the governance
layer is enabled.

- Floe environment profiles point at OpenMetadata's native endpoint and supply
  the ingestion-bot JWT only through a Kubernetes Secret reference.
- Project-code includes `openlineage-dbt`; Dagster sets the product job name
  around the dbt invocation so events have stable logical identities.
- OpenMetadata is responsible for entity resolution and lineage persistence.
  Product descriptors and generated Floe manifests remain provider-neutral and
  contain no credentials.
- The local provider uses the `polaris` dataset namespace and the AWS provider
  uses `aws_glue`, matching their catalog identities.

This ADR supersedes ADR 0009 only for the Floe and dbt engine lineage deferral.
Its rejection of a normalising proxy and custom REST edge push remains binding.
Its Dagster and Superset connector findings remain deferred until their upstream
connectors are corrected.

## Consequences

OpenMetadata can resolve the complete Bronze-to-Silver-to-Gold lineage graph
for the supported products. End-to-end validation must continue to exercise
the native endpoint and canonical entity resolution whenever the full governance
profile is enabled.

Lineage is still conditional on the governance layer. Slim deployments omit
the endpoint and JWT rather than emitting to an undeployed service. Dashboard
and Dagster pipeline metadata ingestion are separate concerns and do not become
supported through this decision.

## References

- ADR 0009: OpenMetadata lineage integration deferred
- `libs/floe/profiles/local-k8s.yml`
- `libs/floe/profiles/aws-eks.yml`
- `images/project-code/pyproject.toml`
