# ADR 0005: dbt-duckdb Gold on Dagster Kubernetes Runs

## Status

Accepted

Current implementation note: the Sales-first dbt pattern has been generalized to
product-owned dbt projects. Product Gold marts now write into the `gold`
namespace of the shared `lakehouse_dev` warehouse.

## Context

Iteration 4 must extend the Sales POC from Floe-owned Silver Iceberg tables to
dbt-owned Gold marts while preserving the existing Kubernetes-native execution
model. The roadmap calls for dbt-duckdb Gold models orchestrated by Dagster, and
the local acceptance path requires Trino to query the final Gold Iceberg tables.

## Decision

Sales Gold transformations use `dbt-duckdb` inside the existing
`project-code` image. Dagster launches dbt work in isolated Kubernetes run pods
through the existing `K8sRunLauncher`; Iteration 4 does not introduce a separate
dbt runner image.

DuckDB attaches Polaris at runtime and transforms Silver Iceberg tables into
Gold Iceberg tables through the Polaris REST catalog. Silver resides in the `silver` namespace of the `lakehouse_dev` warehouse, while Gold
marts are written to the `gold` namespace of the same warehouse.

Polaris owns a dedicated dbt service principal and Kubernetes Secret
`polaris-dbt-creds`, separate from Floe and Trino credentials.

The durable end-to-end jobs are product-scoped, such as
`sales_order_revenue_pipeline`, `sales_customer_health_pipeline`, and
`supply_chain_inventory_reliability_pipeline`. They materialize Bronze source
assets, Floe Silver assets, and dbt Gold assets in product-specific Dagster
asset groups. Trino remains the SQL query engine for inspecting and validating
the resulting Iceberg tables.

## Consequences

The v1 runtime still uses one OpenLakeForge project-code image for domain code,
Dagster, dlt, dagster-floe, dbt-duckdb, and dagster-dbt.

DuckDB runs on Kubernetes because it executes inside Dagster run pods. A
dedicated transform runner image remains a possible later hardening step after
the dbt Iceberg write path is proven.

Static dbt parse and compile checks do not require a live local stack. The
Polaris attach macro is enabled only for real local runtime execution through
`OPENLAKEFORGE_DBT_ATTACH_POLARIS=true`.
