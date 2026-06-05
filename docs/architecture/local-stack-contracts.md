# Local Stack Contracts

The local lakehouse stack is assembled by Terraform in
`infra/terraform/environments/local`. Service modules exchange contracts through
Kubernetes service DNS names and Kubernetes Secrets, not generated files.

## Storage Contract

SeaweedFS exposes the local S3-compatible API at:

```text
http://seaweedfs-s3:8333
```

The storage module owns:

- S3 access credentials in `seaweedfs-s3-creds`
- the Iceberg bucket `iceberg-data`
- the local code/artifact bucket `openlakeforge-code`
- path-style S3 access
- region `us-east-1`

Downstream services consume only the endpoint, bucket name, region, and Secret
key references.

## Catalog Contract

Polaris exposes the Iceberg REST catalog at:

```text
http://polaris:8181/api/catalog
```

The catalog module owns:

- root bootstrap credentials in `polaris-bootstrap-credentials`
- the `lakehouse` catalog
- the Trino service principal and role grants
- Trino OAuth credentials in `polaris-trino-creds`
- the Floe service principal and role grants
- Floe OAuth credentials in `polaris-floe-creds`
- the dbt service principal and role grants
- dbt OAuth credentials in `polaris-dbt-creds`

Trino, Floe, and dbt consume only the REST URI, token URI, warehouse name, OAuth
scope, and Secret key references.

## Query Contract

Trino exposes SQL over HTTP at:

```text
http://trino:8080
```

The Trino Iceberg catalog uses environment-variable secret substitution for all
credentials. The mounted catalog file must contain placeholders such as
`${ENV:AWS_ACCESS_KEY_ID}` rather than literal secret values.

## Reporting Contract

Superset exposes the local BI UI over HTTP at:

```text
http://superset:8088
```

Superset uses the shared PostgreSQL service for metadata and chart-managed Redis
for local cache and worker support. Sales report assets are not seeded by
Terraform bootstrap. They are source-controlled under
`domains/sales/reports/superset/`, copied into the Superset reports PVC at
`/app/openlakeforge/reports`, and imported by the local/CD report deployment
step.

## Orchestration Contract

Dagster exposes the local UI and GraphQL API over HTTP at:

```text
http://dagster-dagster-webserver:80
```

The orchestration module owns:

- the Dagster Helm release
- shared PostgreSQL credentials for Dagster metadata
- the Sales code server loading `domains.sales.pipelines.dagster.definitions`
- the Kubernetes run launcher
- the local project-code image reference `ghcr.io/openlakeforge/project-code:local`
- the Sales Floe manifest URI `s3://openlakeforge-code/floe/sales/sales.manifest.json`

Local development loads the image into kind and uses the Dagster UI to launch
`sales_etl_pipeline` for the full Sales path. Dagster loads the Floe asset graph
from the manifest baked into the project-code image. Terraform provisions the
code bucket and passes the remote manifest URI to Dagster; `make local-up`
publishes the same generated Sales Floe manifest to that URI after Terraform
applies the stack so the separate Floe runner pod can read it. Dagster launches
Floe Kubernetes jobs from `ghcr.io/malon64/floe:0.4.6`. dbt-duckdb runs inside
Dagster Kubernetes run pods from the project-code image and writes Gold Iceberg
marts to the `sales_gold` Polaris namespace.
