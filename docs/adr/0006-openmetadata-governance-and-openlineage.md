# ADR 0006: OpenMetadata Governance and OpenLineage Integration

## Status

Accepted

## Context

Iteration 5 must add metadata governance and lineage observability to the Sales POC
without requiring external SaaS services. The platform already emits lineage naturally
at two points: Floe runners writing Silver Iceberg tables and dbt-duckdb writing Gold
Iceberg tables. The goal is to surface those events in a governed catalog with asset
discovery and end-to-end lineage.

OpenMetadata was selected as the governance target in ADR 0001. Its native OpenLineage
ingest endpoint accepts standard OpenLineage events, but two integration constraints
shaped the implementation:

- Floe run IDs are not UUID v4. OpenMetadata's OpenLineage endpoint requires a valid UUID
  in the `runId` field, so raw Floe run IDs cause ingestion failures.
- PyIceberg 0.5.1 (used by OpenMetadata's Iceberg connector) does not support the OAuth
  scope format required by Polaris. Minting a short-lived OAuth token at bootstrap time
  and injecting it directly into the service connection is the only working path for the
  current dependency set.

## Decision

OpenMetadata 1.12.10 is deployed via the official Helm chart with OpenSearch as its
search backend and the shared PostgreSQL instance as its metadata store.

A custom OpenLineage proxy is deployed alongside OpenMetadata to normalise lineage
events before they reach OpenMetadata. The proxy:

- Receives `POST /api/v1/lineage` from Floe runner pods and dbt runs.
- Rewrites non-UUID run IDs using UUID v5 with the namespace
  `openlakeforge:floe:{run_id}`.
- Forwards authenticated requests to the native OpenMetadata OpenLineage endpoint
  using the ingestion-bot JWT.
- Exposes a stable cluster-internal address at `http://openmetadata-openlineage:5000`.

A Kubernetes bootstrap Job runs after the Helm release is ready. It:

- Provisions the Polaris catalog service in OpenMetadata by minting a short-lived
  Polaris OAuth token at bootstrap time and injecting it into the service connection.
  This works around the PyIceberg 0.5.1 OAuth scope incompatibility with Polaris.
- Pre-seeds the `sales_dev` database and `silver` / `gold` schemas so that lineage
  asset references have a home before the first pipeline run.
- Registers the OpenLineage pipeline service and the dbt pipeline entity.
- Optionally schedules an hourly Polaris catalog crawl for table schema refresh.

Dagster injects `OPENLINEAGE_URL`, `OPENLINEAGE_ENDPOINT`, and `OPENLINEAGE_API_KEY`
into both code-location containers and Kubernetes run pods so that all execution layers
share one lineage destination without per-domain configuration.

Floe reads its lineage target from the `local-k8s.yml` profile, which references the
same env-var-substituted credentials injected by Dagster.

dbt runs use `dbt-ol` (the `openlineage-dbt` plugin) when `OPENLINEAGE_URL` is set in
the environment, and plain `dbt` otherwise. This keeps CI and parse-only checks free of
OpenLineage dependency without a code branch.

## Consequences

The governance module exposes two contract outputs consumed by the orchestration module:
`openlineage_url` and the ingestion-bot JWT secret reference. Orchestration depends on
the governance module for those values.

The OpenLineage proxy is a thin, stateless normalisation layer. It has no persistent
state; restarting it drops in-flight events only.

The bootstrap Job is not idempotent in all cases. Re-running it against a live
OpenMetadata instance may produce duplicate pipeline entity registrations. Tear-down
and re-provision is the expected local recovery path.

Keycloak SSO integration for OpenMetadata is out of scope for Iteration 5 and is
deferred to a later hardening iteration.
