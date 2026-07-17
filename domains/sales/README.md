# Sales Domain

The Sales domain contains two v1 proof-of-concept data products:

- `order_revenue`
- `customer_health`

It proves the path from example CSV data to Bronze landing, Floe-validated
Silver Iceberg tables, dbt-trino Gold marts, Trino querying, Superset
reporting, and Dagster asset orchestration.

## Domain Contract

```text
domains/sales/
├── domain.yaml
├── contracts/floe/
├── examples/raw/
├── extract/dlt/
├── transformations/dbt/
├── pipelines/dagster/
└── reports/superset/
```

Each data product owns:

- raw CSV examples under `examples/raw/<product>`
- dlt-backed Bronze source assets under `extract/dlt/<product>.py`
- Floe contracts and generated manifests under `contracts/floe`
- dbt-trino Gold marts under `transformations/dbt/<product>/models/gold`
- Dagster definitions under `pipelines/dagster/<product>.py`
- Superset report assets under `reports/superset/<product>`
- OpenMetadata product and table metadata nested in `domain.yaml`

Run `make dbt-parse` before building the project-code image when you want the
generated dbt manifests baked into the local image. The product jobs are
`sales_order_revenue_pipeline` and `sales_customer_health_pipeline`.
