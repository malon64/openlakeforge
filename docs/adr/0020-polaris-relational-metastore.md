# ADR 0020: Polaris Relational Metastore for Local and Azure

## Status

Accepted

## Context

Polaris owns Iceberg table identity for the local and Azure POC profiles. Its
in-memory metastore lost catalogs, namespaces, principals, and tables whenever
the Polaris pod restarted. Recovery required a full platform re-apply to
recreate the bootstrap Job and could rotate client credentials, while Trino
still held the old credentials.

The local and Azure profiles already run PostgreSQL in-cluster for Dagster,
OpenMetadata, and Superset. AWS is intentionally different: its catalog
contract is implemented by Glue and must not acquire a Polaris dependency.

## Decision

Local and Azure provision a dedicated `polaris` PostgreSQL role and database in
the existing in-cluster PostgreSQL StatefulSet. The PostgreSQL module creates a
`postgresql-polaris-creds` Kubernetes Secret with `username`, `password`, and
`jdbcUrl`. Its provider contract exposes only the Secret name; no credential or
JDBC value is exposed through Terraform outputs or rendered Helm values.

The Polaris Helm release uses `relational-jdbc` and references those Secret
keys. The Polaris module receives this dependency through the metadata-database
contract, so PostgreSQL bootstrap completes before Polaris starts.

The bootstrap Job is repeatable. Existing relational-metastore catalogs,
namespaces, principals, roles, and credential Secrets are retained. Migration
from the former in-memory metastore is deliberately destructive: obsolete
client Secrets are removed and fresh Polaris credentials are created. A
pre-create ConfigMap marker makes the narrow principal-create/Secret-write
failure window retryable: a retry rotates credentials only when that marker
proves the principal was created by an incomplete bootstrap attempt. A
principal with no credential Secret and no marker remains a hard failure so an
active relational-metastore client is never silently rotated.

## Consequences

Deleting and rescheduling the Polaris pod preserves its catalog state while the
PostgreSQL PVC remains intact. Trino does not need a restart or a Terraform
apply after that Polaris restart. The local full e2e suite verifies this path by
restarting Polaris and querying Silver and Gold again.

This is not PostgreSQL durability, backup and restore, high availability,
managed-service adoption, or a production recovery guarantee. Losing the
PostgreSQL PVC still loses the Polaris metastore. Rollback to in-memory Polaris
is destructive to the catalog state and requires a deliberate platform
rebootstrap; it is not an in-place rollback. AWS continues to use Glue without
this database or Secret.
