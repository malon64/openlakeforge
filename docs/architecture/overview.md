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
  -> Superset reports
  -> Dagster asset graph
```

The first local infrastructure target is kind. Iteration 1 stands up the local
Kubernetes foundation before introducing Dagster runs and domain pipelines.
Iteration 2 adds the project-code image, Dagster webserver, Dagster daemon,
sales code server, and Kubernetes run launcher. Iteration 3 adds the Sales dlt
Bronze extract and manifest-first Floe Silver materialization. Iteration 4 adds
Sales dbt-duckdb Gold marts and dagster-dbt orchestration in the same Kubernetes
run-pod execution model.
Iteration 5 adds OpenMetadata governance and catalog discovery. Iteration 6 adds
Superset reporting over Sales Gold marts through Trino. OpenLineage integration
is deferred until upstream connector issues are fixed; see ADR 0009.

## Core Platform Decisions

| Concern | Decision |
| --- | --- |
| Deployment model | Kubernetes-native from the beginning |
| Portability | Cloud-agnostic across local, on-prem, and cloud Kubernetes |
| Processing scope | Batch-first v1; streaming deferred |
| Table format | Apache Iceberg |
| Catalog | Iceberg catalog contract; local uses Apache Polaris REST |
| Local object storage | SeaweedFS |
| Query layer | Trino for analytics queries only |
| Reporting | Superset over Gold marts through Trino |
| Orchestration | Dagster asset graph |
| Lineage protocol | OpenLineage target, currently deferred |
| Governance target | OpenMetadata |
| IAM target | Keycloak later; local uses development credentials |
| Secret management target | Vault / External Secrets later; local uses Kubernetes Secrets |
| Ingress and TLS target | Traefik / cert-manager later; local uses port-forwarding |

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
- dagster-dbt connector
- Floe contracts
- dbt project and models
- dlt pipelines
- domain Python code
- shared OpenLakeForge libraries

The project-code image does not install the Floe CLI. It includes the generated
Sales Floe manifest so the Dagster code server can load the asset graph from the
image. Because Floe work runs in a separate manifest-declared
`ghcr.io/malon64/floe:0.4.6` Kubernetes runner image, local/CD artifact upload
also publishes the generated Sales manifest to SeaweedFS for the runner pod.

The expected runtime flow is:

```text
Dagster webserver / daemon / code server
  -> Kubernetes run launcher
  -> isolated Dagster run pod using project-code image
  -> dlt ingestion assets
  -> Floe assets through dagster-floe and the Floe runner image
  -> dbt assets through dagster-dbt
  -> metadata catalog refresh
```

The local stack loads `ghcr.io/openlakeforge/project-code:local` into the local
kind cluster and uses it for both the Sales code server and isolated Dagster run
pods. The durable Sales job is `sales_etl_pipeline` under
`domains/sales/pipelines/dagster`. It materializes Sales Bronze source assets,
executes manifest-loaded Floe assets for `sales`, `customers`, and `products`,
then runs dbt-duckdb Gold marts in the `gold` Polaris namespace of the `sales_dev` warehouse.

Superset is deployed as a BI consumer of those Gold marts through Trino. Sales
Superset reports are dynamic domain artifacts under
`domains/sales/reports/superset/` and are deployed separately from Terraform
bootstrap so UI edits can be exported back to source control when they become
durable report changes.
