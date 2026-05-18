# OpenLakeForge Architecture Overview

OpenLakeForge is a cloud-agnostic, self-hostable lakehouse platform built from open-source components. The platform is Kubernetes-native and is intended to support local, on-prem, and cloud Kubernetes deployments through Terraform and Helm.

## v1 Product Shape

The v1 proof of concept proves the complete batch lakehouse path for one domain:

```text
CSV examples
  -> Bronze landing
  -> Floe validation
  -> Silver Iceberg tables through Polaris
  -> dbt-duckdb Gold marts
  -> Trino query
  -> Dagster asset graph
```

The first local infrastructure target is `k3d`. Iteration 1 will use it to stand up the local Kubernetes foundation before introducing Dagster runs and domain pipelines.

## Core Platform Decisions

| Concern | Decision |
| --- | --- |
| Deployment model | Kubernetes-native from the beginning |
| Portability | Cloud-agnostic across local, on-prem, and cloud Kubernetes |
| Processing scope | Batch-first v1; streaming deferred |
| Table format | Apache Iceberg |
| Catalog | Apache Polaris REST catalog |
| Local object storage | SeaweedFS |
| Query layer | Trino for analytics queries only |
| Orchestration | Dagster asset graph |
| Lineage protocol | OpenLineage |
| Governance target | OpenMetadata |
| IAM target | Keycloak |
| Secret management target | Vault and External Secrets Operator |
| Ingress and TLS target | Traefik and cert-manager |

## Medallion Ownership

Bronze is an immutable landing zone owned by ingestion. Silver is owned by Floe and contains technically validated Iceberg tables. Gold is owned by dbt and contains business-ready marts and analytics models.

dbt does not own a Silver staging layer in v1. Floe writes Silver Iceberg tables directly through the Polaris REST catalog. dbt consumes those Silver tables and builds Gold models.

## Execution Model

OpenLakeForge v1 uses one custom runtime image:

```text
ghcr.io/openlakeforge/project-code:<tag>
```

The image will contain:

- Dagster code
- dagster-floe / Floe connector
- Floe contracts
- dagster-dbt
- dbt-duckdb project
- dlt pipelines
- domain Python code
- shared OpenLakeForge libraries

The expected runtime flow is:

```text
Dagster webserver / daemon / code server
  -> Kubernetes run launcher
  -> isolated Dagster run pod using project-code image
  -> dlt ingestion assets
  -> Floe assets through dagster-floe
  -> dbt assets through dagster-dbt
  -> lineage and metadata emission
```

Separate Floe and dbt runner images are not part of the v1 baseline.
