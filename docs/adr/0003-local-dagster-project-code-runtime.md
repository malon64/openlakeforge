# ADR 0003: Local Dagster Project-Code Runtime

## Status

Accepted

## Context

Iteration 2 must prove that OpenLakeForge can run domain-owned Dagster code in
Kubernetes using the single v1 `project-code` runtime image.

The v1 baseline intentionally avoids separate Floe, dbt, and ingestion runner
images. The first orchestration milestone should therefore validate the runtime
image and Kubernetes run launcher boundary before adding Sales ingestion,
Floe, Iceberg writes, or dbt models.

## Decision

OpenLakeForge deploys Dagster in the local Terraform-managed stack using the
official Dagster Helm chart.

The local Dagster deployment uses:

- Dagster chart version `1.13.6`.
- Matching `dagster==1.13.6` in the project-code Python package.
- Chart-managed PostgreSQL for local Dagster metadata.
- Dagster webserver, daemon, and one sales code server.
- `K8sRunLauncher` for isolated run pods.
- Local image loading into kind for `ghcr.io/openlakeforge/project-code:local`.
- The project-code image for the Dagster webserver, daemon, code server, and run
  pods so all Dagster components use the same pinned Python dependency set.

The first domain-owned Dagster code lives under:

```text
domains/sales/pipelines/dagster
```

It exposes a minimal no-data smoke job named `iteration2_smoke_job`. This job
exists only to prove that Dagster can load the sales code location and launch an
isolated Kubernetes run pod from the project-code image.

## Consequences

Iteration 2 validates the execution boundary without starting Sales ingestion or
Silver/Gold processing.

The local workflow now requires Docker, kind, kubectl, Terraform, Helm, and
Python in the shell used to run the Make targets.

GHCR publication remains out of scope for Iteration 2. The image name is shaped
for the final registry contract, but local success uses `kind load docker-image`.
