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

The first local infrastructure target is kind. Iteration 1 stands up the local
Kubernetes foundation before introducing Dagster runs and domain pipelines.
Iteration 2 adds the project-code image, Dagster webserver, Dagster daemon,
sales code server, and Kubernetes run launcher. Iteration 3 adds the Sales dlt
Bronze extract and manifest-first Floe Silver materialization.

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

The image contains:

- Dagster code
- dagster-floe connector
- Floe contracts
- dlt pipelines
- domain Python code
- shared OpenLakeForge libraries

The project-code image does not install the Floe CLI. Floe manifests are
generated before image build, and Floe work runs from the manifest-declared
`ghcr.io/malon64/floe:0.4.2` Kubernetes runner image.

The expected runtime flow is:

```text
Dagster webserver / daemon / code server
  -> Kubernetes run launcher
  -> isolated Dagster run pod using project-code image
  -> dlt ingestion assets
  -> Floe assets through dagster-floe and the Floe runner image
  -> dbt assets through dagster-dbt in a later iteration
  -> lineage and metadata emission
```

Iteration 2 loads `ghcr.io/openlakeforge/project-code:local` into the local kind
cluster and uses it for both the Sales code server and isolated Dagster run
pods. The first job is `iteration2_smoke_job` under
`domains/sales/pipelines/dagster`, and it has no data dependencies.

Iteration 3 adds `iteration3_sales_silver_job`, which materializes Sales Bronze
source assets and then executes manifest-loaded Floe assets for `sales`,
`customers`, and `products`.
