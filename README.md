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
├── pipelines/dagster/
└── tests/
```

## Runtime Boundary

The project-code image is built as:

```text
ghcr.io/openlakeforge/project-code:<tag>
```

It contains Dagster code, `dagster-floe`, Floe contracts, dlt extract code,
domain Python code, and shared OpenLakeForge libraries. It does not install the
Floe CLI; Floe runs from the manifest-declared GHCR runner image.

## Roadmap

- Iteration 0: repository skeleton, architecture documentation, and validation automation.
- Iteration 1: local kind foundation with namespaces, SeaweedFS, Polaris, and Trino.
- Iteration 2: project-code image and Dagster deployment with Kubernetes run launcher.
- Iteration 3: Sales POC ingestion and Floe Silver materialization.
- Iteration 4: dbt-duckdb Gold models and Dagster-dbt integration.

## Local Validation

```sh
make check-structure
make local-cluster
make floe-manifest
make project-code-image
make project-code-load
make local-up
```

The local shell must have Docker, kind, kubectl, Terraform, Helm, Python, and
the `floe` CLI available. The Dagster UI is available at `http://localhost:3000`
through `make local-forward`. Launch `sales_bronze_to_silver_job` from Dagster
to run the Sales `dlt -> Floe -> Silver Iceberg` pipeline. Trino is forwarded to
`http://localhost:8080` for local SQL clients such as DBeaver.
