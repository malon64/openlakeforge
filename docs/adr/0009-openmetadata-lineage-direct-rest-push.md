# ADR 0009: OpenMetadata Lineage Integration Deferred (replaces OL proxy from ADR 0006)

## Status

Accepted

## Context

ADR 0006 described an OpenLineage proxy deployed alongside OpenMetadata that normalised
Floe and dbt events before forwarding them to OpenMetadata's native OL endpoint. During
Iteration 6 the proxy approach was found to mask a deeper set of correctness problems in
the upstream tools, producing incomplete or incorrectly attached lineage in OpenMetadata
despite the proxy running successfully.

### Problems found with direct OpenLineage emission

#### Floe 0.4.6 — four bugs in OL emission

**1. Non-UUID run IDs**
Floe generates run IDs like `mfv1-78d9d79c2db585a7-customers-001`. The OpenLineage spec
requires `runId` to be a UUID v4. The proxy worked around this with UUID v5 re-hashing,
but it is a source-level bug that should be fixed in Floe.

**2. Empty `inputs` and `outputs` arrays**
Floe emits `START` and `COMPLETE` events with `"inputs": []` and `"outputs": []`.
Neither the S3 Bronze source path nor the Iceberg Silver sink is included. Without
dataset references, no lineage edge can be derived by any consumer regardless of how the
proxy normalises other fields. This is the root cause of missing Bronze→Silver lineage.

**3. Malformed job name**
Floe sets the OL job name to `{namespace}.{entity}`, e.g.
`http://polaris:8181/api/catalog.customers`. The namespace is a separate OL concept and
must not be prepended to the job name. OpenMetadata creates a pipeline service named
after the full Polaris URI string, which is unidentifiable in the catalog.

**4. S3 dataset name emitted with a leading slash**
Floe emits S3 dataset names as `/bronze/sales/customers` instead of
`bronze/sales/customers`. OpenMetadata resolves S3 datasets by bucket-relative path
without a leading slash, so the pre-seeded SeaweedFS container entities are never
matched. (Originally filed as malon64/floe#382; this ADR extends that report with
items 1–3 above.)

#### openlineage-dbt / dbt-duckdb — one structural mismatch

**5. DuckDB ATTACH Iceberg catalog: dataset namespace uses file path instead of catalog URI**
When `dbt-duckdb` connects to Polaris via `ATTACH`, `openlineage-dbt` derives the OL
dataset namespace from the local DuckDB file path
(`duckdb:///tmp/openlakeforge-sales-dbt.duckdb`) rather than from the attached catalog
endpoint. `OPENLINEAGE_NAMESPACE` only sets the *job* namespace, not the *dataset*
namespace. OpenMetadata cannot match `duckdb:///tmp/...` to the `polaris` service, so
all Silver and Gold dataset references in dbt OL events resolve to wrong or non-existent
OM entities.

The root cause is in `dbt-duckdb`: the adapter knows the ATTACH Iceberg endpoint but
does not expose it through any interface that `openlineage-dbt` can read. A dbt Jinja
macro cannot fix this because the namespace is determined in Python code inside
`openlineage-dbt`, not in the Jinja/SQL compilation layer.
Filed as duckdb/dbt-duckdb#764.

### Why the proxy approach is abandoned

The proxy addressed symptoms 1 and 4 at the network layer but could not address 2, 3,
or 5. Continuing to maintain a normalisation proxy while upstream bugs remain unfixed
creates an invisible maintenance surface and obscures the actual state of lineage quality
from both developers and OM consumers.

### Why a custom Dagster REST push was also abandoned

After removing the proxy, a Dagster-native lineage push (`om_lineage.py`) was
implemented as an alternative: a Dagster asset running after each ETL stage that called
the OM REST API directly to upsert table metadata and lineage edges. The result was worse
than both the proxy approach and having no lineage at all — incomplete graphs, stale
edges from partial runs, and column-level metadata that diverged from what the Polaris
crawler later discovered. The added maintenance cost of a bespoke REST-push layer that
duplicates logic already present in the upstream connectors is not justified.

## Decision

All lineage integration with OpenMetadata is removed for the current iteration.

- The OpenLineage proxy (`openmetadata-openlineage` Kubernetes deployment) is removed.
- Floe OL emission is disabled by removing the `lineage:` block from the Floe profile
  (`domains/sales/contracts/floe/profiles/local-k8s.yml`).
- The `openlineage-dbt` package is removed from `images/project-code/pyproject.toml`.
- The custom `om_lineage.py` Dagster module is deleted.
- Dagster receives no `OPENMETADATA_URL`, `OPENMETADATA_TOKEN`, or `OPENLINEAGE_*`
  environment variables.
- The governance module contract no longer exports lineage-related outputs.

OpenMetadata remains deployed and receives pre-seeded catalog entities (service, database,
schemas, table stubs, domain, data products, SeaweedFS storage service, and Bronze CSV
containers) via the bootstrap Job and the `openmetadata-metadata-deploy` artifact script.
Lineage graphs in OM will remain empty until upstream fixes land and lineage is
re-enabled.

## Dagster and Superset metadata ingestion also deferred

Beyond lineage, the metadata crawl for Dagster (pipeline metadata) and Superset (dashboard
metadata) is also non-functional in OM 1.12.x due to connector bugs in the OM ingestion
package.

### Dagster connector — GraphQL type name mismatch

The OM 1.12.x Dagster connector sends a test query using the fragment
`... on PipelineRuns { results { ... } }`. Dagster 1.x renamed this union type from
`PipelineRuns` to `Runs`. The fragment never matches, the test connection step crashes,
and ingestion never starts. Both services and ingestion pipeline definitions are
pre-registered in OM (visible in Settings → Services) but not triggered until the
connector is updated.

### Superset connector — Java API / Python SDK credential schema mismatch

The OM 1.12.x REST API (`PUT /api/v1/services/dashboardServices`) accepts only `hostPort`
in the `SupersetConnection` config; any `username` or `password` field at the same level
returns HTTP 400 "unrecognized field". However, the OM Python ingestion SDK's
`SupersetConnection` Pydantic model has `username` as a required field. When the ingestion
job reads back the stored connection (which lacks `username`), the discriminated-union
deserializer cannot construct a valid `SupersetConnection` object and
`config.serviceConnection.root` is `None`, crashing the connector before any dashboards
are indexed.

The Superset service and ingestion pipeline definition are pre-registered in OM and will
work once the credential schema is aligned between the Java and Python layers.

## Upstream issues to watch

| Issue | Repository | What unblocks |
|---|---|---|
| malon64/floe#382 + comment | malon64/floe | All four Floe OL bugs (non-UUID run ID, empty inputs/outputs, malformed job name, S3 leading slash) |
| duckdb/dbt-duckdb#764 | duckdb/dbt-duckdb | ATTACH endpoint exposed to openlineage-dbt so dataset namespace resolves to the catalog URI |
| OM Dagster connector | open-metadata/OpenMetadata | `... on Runs` fragment instead of `... on PipelineRuns` for Dagster 1.x |
| OM Superset connector | open-metadata/OpenMetadata | Align Java API schema to accept `username`/`password` OR make Python SDK not require them |

When lineage issues are resolved, the expected path forward is:

1. Re-enable Floe OL by restoring the `lineage:` block in the Floe profile.
2. Re-add `openlineage-dbt` and configure `OPENLINEAGE_*` env vars in Dagster.
3. Remove the OL proxy entirely (it is already gone); route OL events directly to the OM
   native endpoint at `/api/v1/openlineage/lineage`.

When Dagster/Superset connector bugs are resolved, trigger the pre-registered ingestion
pipelines (`dagster.dagster-metadata-ingestion`, `superset.superset-metadata-ingestion`)
from the OM UI or via the API.

## Consequences

OpenMetadata browse and search are fully functional. Domains, data products, table stubs,
and the SeaweedFS storage hierarchy are seeded at stack startup. The Polaris catalog
crawler refreshes column metadata on an hourly schedule.

Lineage graphs are empty. Dagster pipeline and Superset dashboard metadata is not imported.
These are blocked by upstream connector bugs and will be unblocked when those bugs are fixed.
