# Local Provider Contract Implementation

The local lakehouse stack is one implementation of the provider contracts
described in `provider-contracts.md`. It is assembled by Terraform in
`infra/terraform/environments/local`. Service modules exchange contracts through
Kubernetes service DNS names and Kubernetes Secrets, not generated files.

The local root normalizes provider contracts in `contracts.tf`. The foundation
contract is read from the local kind foundation Terraform state, and runtime
scripts load contract-derived defaults through
`scripts/local/contracts/load-runtime-env.sh`.

Local remains the only runnable environment today. Its credentials, basic app
auth, and port-forwarded access are development-only choices, not production
controls.

## Cluster Contract

Local uses a kind cluster created by the Terraform root
`infra/terraform/foundations/local-kind`, exposed as
`make local-foundation-up`. The platform Terraform root
`infra/terraform/environments/local` then applies OpenLakeForge into the
`kind-openlakeforge-local` Kubernetes context. A future cloud implementation
should keep the same two-root model, but its foundation would create provider
networking and a managed Kubernetes cluster before the platform phase runs.

## Storage Contract

SeaweedFS exposes the local S3-compatible API at:

```text
http://seaweedfs-s3:8333
```

The storage module owns:

- S3 access credentials in `seaweedfs-s3-creds`
- the Bronze landing bucket `lakehouse-bronze` (raw CSV files, owned by ingestion)
- the Silver Iceberg bucket `lakehouse-silver` (Floe-validated tables, owned by Floe)
- the Gold Iceberg bucket `lakehouse-gold` (dbt business marts, owned by dbt)
- the local ops/artifact bucket `openlakeforge-ops`
- path-style S3 access
- region `us-east-1`

Downstream services consume only the endpoint, bucket names, region, and Secret
key references. They should not assume SeaweedFS beyond the local provider
profile.

Product Floe contracts refer to the Bronze bucket through the logical
`lakehouse_bronze` storage alias, to the Silver bucket through
`lakehouse_silver`, and to the ops bucket through `openlakeforge_ops` for Floe
run reports.

The local SeaweedFS module also exposes the built-in Filer and Master HTTP UIs
through the storage contract. They are local development inspection surfaces,
not production access controls:

- Filer UI: `svc/seaweedfs-filer-client:8888`, port-forwarded to
  `http://localhost:8888`
- Master UI: `svc/seaweedfs-master:9333`, port-forwarded to
  `http://localhost:9333`

Use `make local-forward` for all local services, including the SeaweedFS Filer
and Master UIs. The Filer UI talks directly to SeaweedFS, so it avoids a second
S3 browser component and manual S3 backend credential setup.

## Metadata Database Contract

The local metadata database contract is implemented by the shared in-cluster
PostgreSQL module. Dagster, OpenMetadata, and Superset consume database host,
port, database names, users, and Kubernetes Secret references for passwords.

A future cloud provider can replace this with a managed PostgreSQL-compatible
database if it exposes the same contract.

## Catalog Contract

Polaris exposes the Iceberg REST catalog at:

```text
http://polaris:8181/api/catalog
```

The catalog module owns:

- local catalog type `rest`
- local catalog provider `polaris`
- root bootstrap credentials in `polaris-bootstrap-credentials`
- the `lakehouse_dev` warehouse with `default-base-location: s3://lakehouse-silver/`
- product Silver namespaces such as `sales_order_revenue_silver` with storage
  locations under `s3://lakehouse-silver/<namespace>/`
- product Gold namespaces such as `sales_order_revenue_gold` with storage
  locations under `s3://lakehouse-gold/<namespace>/`
- allowed S3 locations `s3://lakehouse-silver/` and `s3://lakehouse-gold/`
- the Trino service principal and role grants
- Trino OAuth credentials in `polaris-trino-creds`
- the Floe service principal and role grants
- Floe OAuth credentials in `polaris-floe-creds`
- the dbt service principal and role grants
- dbt OAuth credentials in `polaris-dbt-creds`

Trino, Floe, and dbt consume the generic catalog type/provider metadata plus the
local Polaris REST URI, token URI, warehouse name, OAuth scope, and Secret key
references. Future provider profiles can satisfy the same catalog contract with
another Iceberg catalog implementation, such as AWS Glue, without requiring
local to stop using Polaris.

Product Floe contracts refer to this implementation through the logical
`iceberg_catalog` alias. dbt runtime profiles consume
`OPENLAKEFORGE_CATALOG_*` environment variables instead of Polaris-specific
runtime variable names.

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
for local cache and worker support. Product report assets are not seeded by
Terraform bootstrap. They are source-controlled under
`domains/<domain>/reports/superset/<product>/`, copied into the
Superset reports PVC at `/app/openlakeforge/reports`, and imported by the
local/CD report deployment step.

OpenMetadata receives the Superset service connection during governance
bootstrap, but reports appear in OpenMetadata only after the Superset dashboard
metadata ingestion pipeline crawls the running Superset instance. Importing a
report bundle into Superset and crawling it into OpenMetadata are intentionally
separate steps.

Superset uses local development credentials by default. A future identity
provider implementation should replace this through an identity contract rather
than changing report artifact ownership.

## Orchestration Contract

Dagster exposes the local UI and GraphQL API over HTTP at:

```text
http://dagster-dagster-webserver:80
```

The orchestration module owns:

- the Dagster Helm release
- shared PostgreSQL credentials for Dagster metadata
- the `sales-dagster` code location loading `domains.sales.definitions`
- the `supply-chain-dagster` code location loading `domains.supply_chain.definitions`
- the Kubernetes run launcher
- the local project-code image reference `ghcr.io/openlakeforge/project-code:local`
- the product Floe manifest base URI `s3://openlakeforge-ops/floe/manifests`
- S3-backed Dagster compute logs under `s3://openlakeforge-ops/logs/dagster/compute`

Local development uses `make local-platform-up` for Terraform-managed platform
resources and `make local-artifacts-deploy` for dynamic domain artifacts.
Dagster loads the Floe asset graphs from manifests baked into the project-code
image. Terraform provisions the ops bucket and passes remote artifact base URIs
to Dagster; the artifact deploy phase publishes generated product Floe
manifests under `floe/manifests/` so the separate Floe runner pod can read them.
Dagster launches Floe Kubernetes jobs from the image declared in the generated
Floe manifests.
dbt-duckdb runs inside Dagster Kubernetes run pods from the project-code image
and writes Gold Iceberg marts to each product's Gold namespace in the
`lakehouse_dev` Polaris warehouse.

## Observability Contract

The local observability adapter is `observability.object_log_archive`. It does
not deploy Loki, Grafana, Prometheus, or tracing. Dagster compute logs, Floe
reports, dbt run artifacts, and Kubernetes pod logs are archived to
`openlakeforge-ops` using stable prefixes:

- `logs/dagster/compute/`
- `logs/k8s/namespace=<namespace>/date=<YYYY-MM-DD>/hour=<HH>/`
- `floe/reports/<domain>/<product>/`
- `run-artifacts/dbt/<domain>/<product>/<dagster_run_id>/`

## Secrets, Identity, and Access Contracts

Local secrets are Kubernetes Secrets generated by Terraform and bootstrap jobs.
Local identity is development-only basic authentication for services that need
an initial user. Local access is `kubectl port-forward` through
`make local-forward`.

Future provider profiles should satisfy equivalent secrets, identity, and access
contracts without forcing local to run Vault, Keycloak, or ingress/TLS services.
