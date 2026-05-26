# Sales Domain

The sales domain is the first v1 proof-of-concept domain.

It will prove the path from example CSV data to Bronze landing, Floe-validated Silver Iceberg tables, dbt-duckdb Gold marts, Trino querying, and Dagster asset orchestration.

## Domain Contract

```text
domains/sales/
├── domain.yaml
├── examples/raw/
├── ingestion/dlt/
├── contracts/floe/
├── transformations/dbt/
├── orchestration/dagster/
└── tests/
```

Iteration 2 adds a minimal Dagster smoke job under
`orchestration/dagster/definitions.py`. The job only proves that the Sales code
location can be loaded from the project-code image and launched as an isolated
Kubernetes run pod.

Data, ingestion pipelines, Floe contracts, dbt models, and real assets start in
later iterations.
