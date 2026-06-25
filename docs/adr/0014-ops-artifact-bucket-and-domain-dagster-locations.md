# ADR 0014: Ops Artifact Bucket and Domain Dagster Locations

## Status

Accepted

## Context

OpenLakeForge needed a stronger production-readiness boundary before adding
Loki, Grafana, or a full metrics stack. The POC already had an S3-compatible
artifact bucket for Floe manifests, but its name and contract were scoped too
narrowly to code artifacts.

The platform also loaded all product jobs through one aggregate Dagster code
location. That worked for the seed POC, but it made domain ownership and
deployment isolation less clear.

## Decision

Rename the operational artifact bucket to `openlakeforge-ops` and use stable
prefixes for runtime artifacts:

- `floe/manifests/<domain>/<product>/<product>.manifest.json`
- `floe/reports/<domain>/<product>/`
- `logs/dagster/compute/`
- `logs/k8s/namespace=<namespace>/date=<YYYY-MM-DD>/hour=<HH>/`
- `run-artifacts/dbt/<domain>/<product>/<dagster_run_id>/`

The local observability adapter is `observability.object_log_archive`. It keeps
metrics and tracing disabled and archives logs/reports/artifacts to object
storage first. Loki, Grafana, Prometheus, and provider-native log adapters
remain later observability adapter shapes.

Dagster keeps one shared `project-code` image for v1, but Terraform deploys one
user-code location per domain:

- `sales-dagster` loads `domains.sales.definitions`.
- `supply-chain-dagster` loads `domains.supply_chain.definitions`.

The old aggregate module was removed after the per-domain code locations became
the supported runtime boundary.

## Consequences

Existing local and Azure POC stacks must recreate the old artifact bucket or
copy objects into `openlakeforge-ops` before switching runtimes.

Floe reports now use the `openlakeforge_ops` storage alias instead of
`lakehouse_silver`, so operational reports no longer mix with Silver data.

Dagster compute logs use `S3ComputeLogManager` against the S3-compatible ops
bucket, dbt artifacts are uploaded after successful builds, and a lightweight
Kubernetes CronJob archives pod logs to the same bucket.
