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

Trino and Floe consume only the REST URI, token URI, warehouse name, OAuth scope,
and Secret key references.

## Query Contract

Trino exposes SQL over HTTP at:

```text
http://trino:8080
```

The Trino Iceberg catalog uses environment-variable secret substitution for all
credentials. The mounted catalog file must contain placeholders such as
`${ENV:AWS_ACCESS_KEY_ID}` rather than literal secret values.

## Orchestration Contract

Dagster exposes the local UI and GraphQL API over HTTP at:

```text
http://dagster-dagster-webserver:80
```

The orchestration module owns:

- the Dagster Helm release
- chart-managed local PostgreSQL for Dagster metadata
- the Sales code server loading `domains.sales.pipelines.dagster.definitions`
- the Kubernetes run launcher
- the local project-code image reference `ghcr.io/openlakeforge/project-code:local`

Local validation loads the image into kind and launches `iteration2_smoke_job`.
The smoke job must complete in an isolated Kubernetes run pod.

Iteration 3 validation launches `iteration3_sales_silver_job`. Dagster loads the
generated Sales Floe manifest and the connector launches Floe Kubernetes jobs
from `ghcr.io/malon64/floe:0.4.2`.
