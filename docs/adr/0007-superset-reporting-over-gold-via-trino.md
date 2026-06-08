# ADR 0007: Superset Reporting over Gold Marts via Trino

## Status

Accepted

## Context

Iteration 6 closes the v1 batch lakehouse path by adding a BI layer over the dbt Gold
Iceberg marts produced in Iteration 4. The architecture overview identifies Superset as
the reporting target and Trino as the query engine. The implementation must keep reports
source-controlled and deployable without manual UI work so the local stack can be torn
down and rebuilt reproducibly.

Two constraints shaped the deployment model:

- The upstream Apache Superset Helm chart does not include Trino or PostgreSQL drivers.
  A custom image is required to avoid runtime driver installation.
- Superset's native export format (YAML bundles) is human-readable and diff-friendly,
  making it a viable source-control artifact for charts, datasets, and dashboards.

## Decision

Apache Superset 5.0.0 is deployed via the upstream Helm chart using a custom image that
extends `apache/superset:5.0.0` with the `trino` and `psycopg2-binary` drivers. The
custom image also carries a patch for Trino Iceberg partition discovery.

Superset uses the shared PostgreSQL instance for its metadata store and a chart-managed
Redis instance for local cache and worker support. This is consistent with the pattern
established by OpenMetadata in Iteration 5.

Superset connects to Trino as its sole data source for analytics. The connection is
read-only (`allow_dml: false`) and targets the `iceberg` Trino catalog at
`trino://superset@trino:8080/iceberg`. Superset does not hold a copy of the data; all
queries execute against the Gold Iceberg tables through Trino and Polaris.

Sales reports are source-controlled as YAML bundles under
`domains/sales/reports/superset/`. The bundle covers databases, datasets, charts, and
dashboards. The v1 Sales dashboard (`Sales_Gold_Mart_Overview_1`) contains three charts
built over three Gold marts: `mart_revenue_by_product`, `mart_sales_by_customer`, and
`mart_sales_by_day` in the `sales_gold` Polaris namespace.

Report bundles are not seeded by Terraform bootstrap. They are deployed separately via
`make local-artifacts-deploy`, which copies the bundle into the Superset reports PVC at
`/app/openlakeforge/reports` and imports it through the Superset API. UI edits can be
exported back to source control with `make superset-reports-export`. This separation
keeps Terraform responsible for infrastructure lifetime and the artifact deploy step
responsible for dynamic domain content — the same split used for the Floe manifest.

## Consequences

The analytics module depends on the shared PostgreSQL module and the Trino module.
Superset starts after Trino is ready, but the Trino Iceberg catalog must have valid
Polaris credentials at Superset connection time.

The custom Superset image must be rebuilt whenever the Superset base version changes or
new driver versions are required. The image name follows the same local convention as
`project-code`: built and loaded into kind for local development.

YAML bundle import via the Superset API is not transactional. A partial import leaves
some assets created. The expected recovery path is a full re-import, which overwrites
existing assets by slug.

Superset authentication uses the default local admin credentials. SSO integration via
Keycloak is out of scope for Iteration 6 and is deferred to a later hardening iteration,
consistent with the OpenMetadata decision in ADR 0006.
