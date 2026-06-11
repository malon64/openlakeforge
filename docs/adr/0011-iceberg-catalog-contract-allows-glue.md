# ADR 0011: Iceberg Catalog Contract Allows Glue

## Status

Accepted

## Context

The local OpenLakeForge stack uses Apache Polaris as a self-hosted Iceberg REST
catalog. A future AWS provider profile may prefer AWS Glue Data Catalog instead
of running Polaris.

If platform modules treat the catalog contract as synonymous with Polaris REST,
then replacing Polaris with Glue would require edits across Trino, Dagster,
OpenMetadata, dbt runtime configuration, and documentation. The contract should
therefore describe the Iceberg catalog implementation, not only the current
service.

## Decision

The catalog contract carries generic Iceberg catalog metadata:

- `catalog_type`, using Trino Iceberg catalog values such as `rest` or `glue`;
- `catalog_provider`, such as `polaris` or `aws-glue`;
- `catalog_name` and optional provider-specific identifiers;
- `runtime_profile`, which tells orchestration code which runtime adapter
  profile is expected.

The local implementation sets `catalog_type = "rest"` and
`catalog_provider = "polaris"`. It still exposes Polaris-specific REST and OAuth
fields because local runtime tools need them.

A future Glue implementation must output the same generic catalog fields and set
`catalog_type = "glue"`. Consumers must branch on `catalog_type` instead of
assuming Polaris fields always exist.

## Consequences

Trino catalog rendering can switch between REST and Glue Iceberg catalog
properties from the same contract boundary.

Dagster run pods receive generic `OPENLAKEFORGE_CATALOG_*` environment variables
alongside local Polaris variables. Existing local behavior is unchanged.

OpenMetadata and dbt runtime support for Glue is not implemented in this
iteration. Those consumers now have enough contract metadata to add a Glue
adapter later without changing the local provider profile.
