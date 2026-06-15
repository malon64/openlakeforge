# ADR 0012: Contract-Driven Provider-First Hardening

## Status

Accepted

## Context

OpenLakeForge has a foundation/platform split and provider-neutral contract
outputs, but several consumers still depended directly on local implementation
details such as SeaweedFS endpoints, Polaris runtime variable names, local
artifact upload paths, and the active Kubernetes context.

The next cloud-readiness step should make provider swaps easier without also
trying to replace the chosen v1 services. Dagster, Trino, Superset,
OpenMetadata, dbt-duckdb, and Floe remain the implemented solution stack.

## Decision

Terraform typed contract objects are the source of truth for provider
boundaries. The local platform root normalizes explicit contracts for
foundation, Kubernetes platform, storage, catalog, metadata database, artifact
registry, artifact bucket, secrets, identity, access, observability, query,
orchestration, reporting, and governance.

Local implementations use explicit adapter names such as:

- `foundation.kind`
- `storage.s3_compatible.seaweedfs`
- `catalog.iceberg_rest.polaris`
- `metadata_database.postgresql.in_cluster`
- `secrets.kubernetes_secret`
- `artifacts.local_kind_and_s3`

Future AWS adapter shapes are documented in the same contract layer but are not
runnable in this iteration.

Runtime configuration for Floe, dbt, Superset report import, and artifact
upload is loaded from provider contracts where possible. Product-owned runtime
files use logical names such as `lakehouse_storage` and `iceberg_catalog`; the
local adapter resolves those names to SeaweedFS and Polaris.

## Consequences

Local remains the only runnable environment. No AWS resources, Keycloak, Vault,
remote Terraform state, or cloud secret manager integration is added.

The stack is more provider-ready because consumers now depend on contract env
vars and adapter fields rather than hardcoded local implementation names. Full
service replacement remains out of scope; replacing Dagster, Trino, Superset,
OpenMetadata, dbt, or Floe would need separate solution contracts later.
