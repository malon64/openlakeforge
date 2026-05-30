# Sales Domain

The Sales domain is the first v1 proof-of-concept domain.

It proves the path from example CSV data to Bronze landing, Floe-validated
Silver Iceberg tables, Trino querying, and Dagster asset orchestration.
dbt-duckdb Gold marts start in a later iteration.

## Domain Contract

```text
domains/sales/
├── domain.yaml
├── examples/raw/
├── extract/dlt/
├── contracts/floe/
├── transformations/dbt/
├── pipelines/dagster/
└── tests/
```

Iteration 3 adds:

- raw Sales POC CSV files for `sales`, `customers`, and `products`
- dlt-backed Bronze source assets
- Floe contracts and a generated `floe.manifest.v1` manifest
- Dagster definitions under `pipelines/dagster/definitions.py`

`sales_bronze_to_silver_job` materializes Bronze source assets, then the
manifest-loaded Floe assets write Silver Iceberg tables through Polaris. Query
the local Silver tables from Trino with names such as `iceberg.sales.sales`,
`iceberg.sales.customers`, and `iceberg.sales.products`.
