# OpenLakeForge

OpenLakeForge is a cloud-agnostic, open-source, self-hostable modern lakehouse
platform. It assembles open-source data platform components on Kubernetes with
Terraform and Helm.

![OpenLakeForge Architecture](/docs/assets/openlakeforge_archi.png)

The v1 proof of concept focuses on a local Kubernetes lakehouse path across
multiple domain-owned data products:

```text
CSV examples
  -> Bronze landing
  -> Floe validation
  -> Silver Iceberg tables through Polaris
  -> dbt-duckdb Gold marts
  -> Trino query
  -> Superset reports
  -> Dagster asset graph
```

The current seed POC contains two Sales data products, `order_revenue` and
`customer_health`, plus one Supply Chain data product,
`inventory_reliability`.

## v1 Stack

| Layer | Choice | v1 Role |
| --- | --- | --- |
| Extraction | dlt | Default ingestion framework |
| Technical contracts | Floe | Bronze-to-Silver validation and Silver materialization |
| Transformation | dbt-duckdb | Silver-to-Gold business models |
| Table format | Apache Iceberg | Open table format |
| Catalog | Apache Polaris | Iceberg REST catalog |
| Object storage | SeaweedFS | Default local S3-compatible backend |
| Query serving | Trino | Analytics query engine |
| Reporting | Superset | BI reports over Gold marts |
| Orchestration | Dagster | Asset graph and run orchestration |

## Repository Structure

```text
openlakeforge/
├── docs/
├── infra/
├── images/project-code/
├── libs/
├── domains/
│   ├── sales/
│   └── supply_chain/
└── scripts/
```

Each domain follows this shape:

```text
domains/<domain>/
├── domain.yaml
├── contracts/floe/
├── examples/raw/<product>/
├── extract/dlt/<product>.py
├── transformations/dbt/<product>/
├── reports/superset/<product>/
└── pipelines/dagster/<product>.py
```

## Runtime Boundary

The project-code image is built as:

```text
ghcr.io/openlakeforge/project-code:<tag>
```

It contains Dagster code, `dagster-floe`, product Floe contracts, generated
product Floe manifests, dlt extract code, domain Python code, and shared
OpenLakeForge libraries. Terraform provisions the local SeaweedFS code bucket
and passes the runner-facing Floe manifest base URI to Dagster; manifest
publication for the separate Floe runner pod is handled by local/CD artifact
upload.

Superset report assets are also treated as dynamic product artifacts. Their
source lives under `domains/<domain>/reports/superset/<product>/`;
local/CD deployment zips each bundle, copies it into the Superset reports
volume, and imports it into the running Superset instance.

OpenMetadata domain and data-product assets follow the same boundary. Terraform
creates OpenMetadata and the platform services it needs; source-controlled
domain, data-product, Bronze, Silver, and Gold metadata in
`domains/<domain>/domain.yaml` is deployed by the local/CD artifact phase.

## Roadmap

- Iteration 0: repository skeleton, architecture documentation, and validation automation.
- Iteration 1: local kind foundation with namespaces, SeaweedFS, Polaris, and Trino.
- Iteration 2: project-code image and Dagster deployment with Kubernetes run launcher.
- Iteration 3: Sales POC ingestion and Floe Silver materialization.
- Iteration 4: dbt-duckdb Gold models and Dagster-dbt integration.
- Iteration 5: OpenMetadata governance, catalog discovery, and OpenLineage ingestion (OL removed in Iteration 6 — see ADR 0009).
- Iteration 6: Superset reporting over Gold marts; OpenLineage integration deferred pending upstream connector fixes.
- Iteration 7: multi-product seed POC with product-owned dlt, Floe, dbt, Dagster, Superset, and OpenMetadata artifacts.

## Local Validation

```sh
make check-structure
make local-foundation-up
make local-up
```

The local shell must have Docker, kind, kubectl, Terraform, Helm, and Python.
The `floe` CLI is optional locally because `make floe-manifest` falls back to
the Floe runner image. `make local-up` runs two phases: `make local-infra-up`
for static Terraform infrastructure, then `make local-artifacts-deploy` for the
project-code image, Floe manifest upload, Superset report import, and
OpenMetadata governance metadata deployment. The Dagster UI is available at
`http://localhost:3000` through `make local-forward`. Launch
`sales_order_revenue_pipeline`, `sales_customer_health_pipeline`, or
`supply_chain_inventory_reliability_pipeline` from Dagster to run product
`dlt -> Floe -> dbt-duckdb` pipelines. Trino is forwarded to
`http://localhost:8080` for local SQL clients such as DBeaver. Superset is
forwarded to `http://localhost:8088` with development-only local credentials
`admin / admin`.
