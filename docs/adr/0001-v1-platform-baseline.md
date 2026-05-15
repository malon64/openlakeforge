# ADR 0001: v1 Platform Baseline

## Status

Accepted

## Context

OpenLakeForge needs a v1 proof of concept that proves a complete lakehouse path while staying open-source, self-hostable, and portable across local, on-prem, and cloud Kubernetes targets.

The first milestone is a local Kubernetes POC, not a production deployment. The platform still needs to make decisions that avoid rewriting the core execution model later.

## Decision

OpenLakeForge v1 adopts the following baseline decisions:

- Kubernetes-native deployment model from the beginning.
- Cloud-agnostic architecture across local, on-prem, and cloud Kubernetes.
- Batch-first v1; streaming is postponed.
- Apache Iceberg is the table format.
- Apache Polaris is the Iceberg REST catalog.
- Garage is the default local S3-compatible object storage backend.
- Trino is used for analytics querying only, not ETL.
- Floe owns Bronze-to-Silver technical validation and Silver materialization.
- dbt owns Silver-to-Gold business transformation.
- Dagster orchestrates domain assets and runs.
- OpenLineage is the lineage protocol.
- OpenMetadata is the governance and catalog UI target.
- Keycloak is the central IAM and SSO layer.
- Vault, External Secrets Operator, Traefik, and cert-manager are product-grade platform requirements.
- v1 uses one custom `images/project-code/` runtime image for domain code, contracts, dbt, dlt, Dagster assets, and shared libraries.
- The first local Kubernetes foundation will target `k3d`.

## Consequences

The repository separates platform infrastructure, shared libraries, custom runtime image code, and domain logic from the start.

Floe is the technical owner of Silver tables. dbt starts from Floe-produced Silver Iceberg tables and builds Gold models only.

Trino remains a serving and analytics query engine. Transformation workloads belong to Floe, dbt, and later optional Spark profiles.

The single project-code image keeps the v1 runtime simple. Separate Floe and dbt runner images can be reconsidered later if operational pressure justifies them.
