# OpenLakeForge

OpenLakeForge is a cloud-agnostic, open-source, self-hostable modern lakehouse
platform. It assembles open-source data platform components on Kubernetes with
Terraform and Helm.

![OpenLakeForge Architecture](/docs/assets/openlakeforge_archi.png)

The v1 proof of concept focuses on a local Kubernetes lakehouse path:

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

Iterations 1 and 2 established the local SeaweedFS, Polaris, Trino, Dagster, and
project-code runtime baseline. Iteration 3 adds the Sales dlt Bronze extract and
manifest-first Floe Silver materialization through Polaris.

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
│   └── sales/
└── scripts/
```

Each domain follows this shape:

```text
domains/<domain>/
├── domain.yaml
├── examples/raw/
├── extract/dlt/
├── contracts/floe/
├── transformations/dbt/
├── reports/superset/
├── governance/openmetadata/
├── pipelines/dagster/
└── tests/
```

## Runtime Boundary

The project-code image is built as:

```text
ghcr.io/openlakeforge/project-code:<tag>
```

It contains Dagster code, `dagster-floe`, Floe contracts, the generated Sales
Floe manifest, dlt extract code, domain Python code, and shared OpenLakeForge
libraries. Terraform provisions the local SeaweedFS code bucket and passes a
runner-facing Sales Floe manifest URI to Dagster; manifest publication for the
separate Floe runner pod is handled by local/CD artifact upload.

Sales Superset report assets are also treated as dynamic domain artifacts. Their
source lives under `domains/sales/reports/superset/`; local/CD deployment zips
the bundle, copies it into the Superset reports volume, and imports it into the
running Superset instance.

OpenMetadata domain and data-product assets follow the same boundary. Terraform
creates OpenMetadata and the platform services it needs; source-controlled
metadata under `domains/sales/governance/openmetadata/` is deployed by the
local/CD artifact phase.

## Roadmap

- Iteration 0: repository skeleton, architecture documentation, and validation automation.
- Iteration 1: local kind foundation with namespaces, SeaweedFS, Polaris, and Trino.
- Iteration 2: project-code image and Dagster deployment with Kubernetes run launcher.
- Iteration 3: Sales POC ingestion and Floe Silver materialization.
- Iteration 4: dbt-duckdb Gold models and Dagster-dbt integration.
- Iteration 5: OpenMetadata governance, catalog discovery, and OpenLineage ingestion (OL removed in Iteration 6 — see ADR 0009).
- Iteration 6: Superset reporting over Sales Gold marts; OpenLineage integration deferred pending upstream connector fixes.

## Local Validation

```sh
make check-structure
make local-cluster
make local-up
```

The local shell must have Docker, kind, kubectl, Terraform, Helm, and Python.
The `floe` CLI is optional locally because `make floe-manifest` falls back to
the Floe runner image. `make local-up` runs two phases: `make local-infra-up`
for static Terraform infrastructure, then `make local-artifacts-deploy` for the
project-code image, Floe manifest upload, Superset report import, and
OpenMetadata governance metadata deployment. The Dagster UI is available at
`http://localhost:3000` through `make local-forward`. Launch
`sales_etl_pipeline` from Dagster to run the Sales `dlt -> Floe -> dbt-duckdb`
pipeline. Trino is forwarded to `http://localhost:8080` for local SQL clients
such as DBeaver. Superset is forwarded to `http://localhost:8088` with
development-only local credentials `admin / admin`.
