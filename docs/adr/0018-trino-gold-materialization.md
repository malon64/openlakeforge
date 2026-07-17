# ADR 0018: Trino Gold Materialization and dbt OpenLineage

## Status

Spike branch: `spike/17-dbt-trino-gold`. Adopt only after the local and AWS
acceptance gates in issue #17 pass.

## Decision

Gold dbt models use the existing Trino service and its `iceberg` catalog. dbt
uses the Trino adapter with `table` materialization and
`on_table_exists: replace`, producing Iceberg `CREATE OR REPLACE TABLE` commits.
The DuckDB adapter, custom Iceberg materialization, Glue UDF/plugin, and dbt
catalog credentials are removed.

`dbt-ol` sends OpenLineage events directly to OpenMetadata's native endpoint.
The Trino dataset namespace is mapped to the canonical provider catalog service
(`polaris` or `aws_glue`), and the Dagster product job name is retained as the
pipeline identity.

Floe OpenLineage is intentionally not changed in this spike. Floe 0.6.8 cannot
target OpenMetadata's native endpoint because it appends `/api/v1/lineage`
unconditionally; this is tracked in [malon64/floe#450](https://github.com/malon64/floe/issues/450).

## Verification

- dbt parses and compiles all three products against Trino profiles.
- A failed replacement preserves the prior Iceberg snapshot and data.
- Repeated replacements produce no unreferenced Gold data files.
- Local and AWS full suites validate Gold tables, Glue/S3 locations, and row counts.
- A captured dbt event resolves to existing OpenMetadata catalog entities and
  creates Silver→Gold lineage without duplicate services or entities.

## Consequences

Trino becomes the Gold compute engine and must be sized for transformation
workloads. Rollback is a branch/image revert followed by a rebuild; Iceberg
snapshots remain available for table-level recovery. Bronze→Silver lineage stays
deferred until Floe supports a configurable native OpenMetadata endpoint.
