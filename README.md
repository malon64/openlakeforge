# OpenLakeForge

OpenLakeForge is a cloud-agnostic, open-source, self-hostable modern lakehouse platform. It assembles a coherent data platform from open-source components, deployed on Kubernetes with Terraform and Helm, and eventually exposed through a unified product UI.

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

Iteration 0 establishes the repository structure and records the first architectural decisions. Runtime infrastructure and local cluster deployment start in Iteration 1, with `k3d` as the default local Kubernetes target.

## Core Principles

- Kubernetes-native from the beginning.
- Cloud-agnostic across local, on-prem, and cloud Kubernetes targets.
- Apache Iceberg is the table format from v1.
- v1 is batch-first; streaming is deferred.
- Trino is the analytics query layer, not the ETL engine.
- Floe owns Bronze-to-Silver technical validation and Silver materialization.
- dbt owns Silver-to-Gold business transformations.
- Dagster orchestrates domain assets.
- OpenLineage is the lineage protocol.
- OpenMetadata is the governance and catalog UI target.
- Keycloak, Vault, External Secrets Operator, Traefik, and cert-manager are product-grade platform requirements.

## v1 Stack

| Layer | Choice | v1 Role |
| --- | --- | --- |
| Extraction | dlt | Default ingestion framework |
| Technical contracts | Floe | Bronze-to-Silver validation and Silver materialization |
| Transformation | dbt-duckdb | Silver-to-Gold business models |
| Table format | Apache Iceberg | Open table format |
| Catalog | Apache Polaris | Iceberg REST catalog |
| Object storage | Garage | Default local S3-compatible backend |
| Query serving | Trino | Analytics query engine |
| Orchestration | Dagster | Asset graph and run orchestration |
| Lineage protocol | OpenLineage | Standard lineage event model |
| IAM | Keycloak | Central SSO and identity layer |
| Secrets | Vault + External Secrets Operator | Secret storage and Kubernetes sync |
| TLS | cert-manager | Certificate automation |
| Ingress | Traefik | Default ingress layer |

Optional or later v1 components include Airbyte, Spark, Superset, OpenMetadata, and Marquez.

## Medallion Ownership

| Layer | Owner | Description |
| --- | --- | --- |
| Bronze | Ingestion | Raw immutable landing zone |
| Silver | Floe | Technically validated Iceberg tables |
| Gold | dbt | Business-ready marts and analytics models |

dbt does not own a Silver staging layer in v1. Floe writes Silver Iceberg tables directly through the Polaris REST catalog. dbt starts from Floe-produced Silver tables and builds only Gold business models and marts.

## Repository Structure

```text
openlakeforge/
├── README.md
├── Makefile
├── docs/
│   ├── architecture/
│   └── adr/
├── infra/
│   ├── terraform/
│   └── helm/
├── images/
│   └── project-code/
├── libs/
├── domains/
│   └── sales/
├── scripts/
└── .github/
    └── workflows/
```

| Path | Purpose |
| --- | --- |
| `docs/architecture/` | Architecture overview and platform shape |
| `docs/adr/` | Architecture decision records |
| `infra/terraform/` | Future Terraform environments and modules |
| `infra/helm/` | Future Helm charts and values |
| `images/project-code/` | Single custom v1 runtime image boundary |
| `libs/` | Shared platform glue, not business logic |
| `domains/` | Domain-owned ingestion, contracts, transformations, assets, and tests |
| `scripts/` | Local validation and developer utility scripts |
| `.github/workflows/` | Repository validation automation |

## Domain Contract

Each domain under `domains/<domain>/` owns business and data-product logic:

```text
domains/<domain>/
├── domain.yaml
├── examples/raw/
├── ingestion/dlt/
├── contracts/floe/
├── transformations/dbt/
├── orchestration/dagster/
└── tests/
```

Shared code belongs in `libs/` only when it is reusable platform glue such as config loading, storage path conventions, Dagster helpers, OpenLineage naming, or observability helpers.

## Runtime Image Boundary

Only one custom runtime image is required in v1:

```text
ghcr.io/openlakeforge/project-code:<tag>
```

The image will contain Dagster code, the Dagster-Floe connector, Floe contracts, Dagster-dbt integration, the dbt-duckdb project, dlt pipelines, domain Python code, and shared OpenLakeForge libraries.

## Roadmap

- Iteration 0: repository skeleton, architecture documentation, and validation automation.
- Iteration 1: local `k3d` foundation with namespaces, PostgreSQL, Garage, Polaris, and Trino.
- Iteration 2: project-code image and Dagster deployment with Kubernetes run launcher.
- Iteration 3: Sales POC ingestion and Floe Silver materialization.
- Iteration 4: dbt-duckdb Gold models and Dagster-dbt integration.
- Iteration 5: OpenLineage naming and lineage emission.
- Iteration 6: interfaces and security with Keycloak, Traefik, cert-manager, Vault, External Secrets Operator, Superset, and OpenMetadata.
- Iteration 7: platform hardening with observability, backup, policy, optional Argo CD, optional Airbyte, and optional Spark.

## Local Validation

Run the Iteration 0 repository contract check:

```sh
make check-structure
```
