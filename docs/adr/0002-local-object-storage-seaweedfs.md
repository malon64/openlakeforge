# ADR 0002: Use SeaweedFS for Local Object Storage

## Status

Accepted

## Context

OpenLakeForge v1 needs a local S3-compatible object store that works reliably with
Apache Polaris, Apache Iceberg, Trino, AWS SDK v2 clients, and Kubernetes Helm
automation.

The initial Iteration 1 implementation used Garage. It was attractive because it
is lightweight and simple to run, but the first end-to-end Polaris and Trino
smoke test exposed compatibility and operational friction:

- Polaris writes through AWS SDK v2 failed against Garage until optional S3
  checksum behavior was disabled with a JVM system property.
- The local chart had to be maintained in this repository instead of relying on a
  mature upstream Helm chart.
- Bootstrap logic had to manage Garage-specific layout, bucket, and key commands.
- The failure mode surfaced as a metadata/catalog problem in Trino even though
  the root cause was S3 compatibility, which makes first-run debugging harder for
  contributors.

This does not mean Garage is a bad project. It remains a reasonable lightweight
self-hosted S3 option. It is just not the best default for OpenLakeForge v1 while
the project is trying to prove a reproducible Iceberg-on-Kubernetes developer
stack.

## Decision

OpenLakeForge v1 uses SeaweedFS as the default local S3-compatible object storage
backend.

Garage is removed from the default local stack. It can be reconsidered later as
an optional storage profile after the platform has stronger storage abstraction,
automated compatibility tests, and documented backend profiles.

## Consequences

The local stack now deploys SeaweedFS from the upstream `seaweedfs/seaweedfs`
Helm chart.

Polaris and Trino point at the SeaweedFS S3 service. S3 credentials are stored in
the `seaweedfs-s3-creds` Kubernetes Secret.

The v1 smoke test target is:

```text
Trino
  -> Polaris Iceberg REST catalog
  -> SeaweedFS S3 bucket
  -> Iceberg metadata and Parquet data
```

The default local platform should now optimize for S3 compatibility, upstream
Kubernetes chart maintenance, and contributor-friendly setup over minimal object
store footprint.
