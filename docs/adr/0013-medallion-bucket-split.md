# ADR 0013: Medallion Bucket Split

## Status

Accepted

## Context

The v1 local lakehouse stored all data in a single `iceberg-data` S3 bucket,
using path prefixes (`bronze/`, `silver/`, `gold/`) to distinguish the three
medallion layers. This created several problems:

- **No per-layer access control.** Bucket policies and IAM roles apply at the
  bucket level. A single bucket cannot restrict ingestion pipelines to bronze
  writes only, or grant BI consumers read-only access to gold without also
  exposing raw bronze data.
- **No per-layer lifecycle policies.** Cloud providers support independent
  retention, tiering, and replication rules per bucket. A single bucket cannot
  apply different cold-storage or retention settings to raw CSVs versus
  validated Iceberg tables.
- **Conflated bucket semantics.** The name `iceberg-data` describes the storage
  backend (Iceberg) rather than the architectural layer, making the bucket's
  purpose ambiguous.
- **Polaris `allowedLocations` too broad.** With a single bucket the Polaris
  catalog allowed any path within that bucket, including paths intended only for
  raw ingestion.

## Decision

Replace `iceberg-data` with three purpose-built medallion buckets:

| Bucket | Layer | Owner | Format |
|---|---|---|---|
| `lakehouse-bronze` | Bronze | dlt (ingestion) | Raw CSV files |
| `lakehouse-silver` | Silver | Floe | Iceberg tables in product Silver Polaris namespaces |
| `lakehouse-gold` | Gold | dbt-duckdb | Iceberg tables in product Gold Polaris namespaces |

The operational artifact bucket is outside the medallion split. It is currently
`openlakeforge-ops`; see ADR 0014 for the rename and ops artifact prefixes.

Within each data bucket, product-owned logical paths follow
`{domain}/{product}/{entity}/` in the Floe contracts and domain descriptors.
The layer is implied by the bucket name, so product contracts do not carry a
`bronze/`, `silver/`, or `gold/` path prefix.

**Polaris catalog** is configured with:
- `default-base-location: s3://lakehouse-silver/`
- `allowedLocations: [s3://lakehouse-silver/, s3://lakehouse-gold/]`
- product Silver namespace locations under `s3://lakehouse-silver/<product>_silver/`
- product Gold namespace locations under `s3://lakehouse-gold/<product>_gold/`

Polaris namespaces are one-level product/layer names such as
`sales_order_revenue_silver` and `sales_order_revenue_gold`. The layer suffix is
still required because a Polaris namespace has a single storage location; Silver
and Gold tables live in separate buckets. The product-owned Floe source and sink
paths remain `{domain}/{product}/{entity}/` without `bronze/`, `silver/`, or
`gold/` path prefixes.

Bronze data is plain CSV and is not registered in the Polaris catalog; the
`lakehouse-bronze` bucket is not in `allowedLocations`.

**Floe profiles** declare two named storage definitions:
- `lakehouse_bronze` → `lakehouse-bronze` bucket (source for all Floe contracts)
- `lakehouse_silver` → `lakehouse-silver` bucket (sink for all Floe contracts)

**Dagster** injects `OPENLAKEFORGE_BRONZE_BUCKET` instead of the former
`OPENLAKEFORGE_S3_BUCKET`. Bronze ingestion code reads this variable. Catalog
env vars are unchanged; ops artifact env vars are covered by ADR 0014.

## Considered Alternatives

**Domain-per-bucket** (e.g. `sales-lakehouse`, `supply-chain-lakehouse`, each
with `bronze/`, `silver/`, `gold/` subdirectories): aligns with data mesh
ownership but requires cross-bucket S3 access for cross-domain joins and adds
operational complexity without benefit at the current two-domain scale.

**Single bucket with finer path structure**: no change to access control
boundaries; rejected because it does not unlock per-layer IAM or lifecycle
policies on any cloud provider.

## Consequences

- Terraform creates four buckets instead of two; existing local stacks must be
  torn down and re-applied (`make local-down && make local-up`).
- The storage contract exposes `bronze_bucket_name`, `silver_bucket_name`, and
  `gold_bucket_name` fields alongside `bucket_name` (which remains the first
  bucket for backward compatibility with generic consumers).
- `load-runtime-env.sh` exports `OPENLAKEFORGE_STORAGE_BRONZE_BUCKET`,
  `OPENLAKEFORGE_STORAGE_SILVER_BUCKET`, and `OPENLAKEFORGE_STORAGE_GOLD_BUCKET`
  from the provider contracts.
- `render-floe-profile.py` renders two storage definitions instead of one.
- A future cloud implementation can map `lakehouse-bronze`, `lakehouse-silver`,
  and `lakehouse-gold` to cloud-native buckets with independent IAM and lifecycle
  policies without changing any domain contract or Floe contract.
