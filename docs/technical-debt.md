# OpenLakeForge Technical Debt

This document tracks known OpenLakeForge weaknesses, current mitigations, and
the intended fix path. ADRs remain the historical decision log; this file is the
current technical debt backlog.

`Resolved` means resolved in OpenLakeForge, not merely fixed upstream.

## OpenLakeForge-Owned Debt

These items can be fixed primarily in this repository without waiting for an
upstream project to change behavior.

| Weakness | Current Impact | Solution | Pending Work | Resolved |
| --- | --- | --- | --- | --- |
| Dynamic artifact revision drift is possible. | Floe manifests are generated, baked into the `project-code` image, and uploaded to the code bucket; manual or partial deploys can make Dagster load one manifest revision while Floe runners execute another. | Enforce one artifact revision contract across the image label, Dagster environment, uploaded manifest hash, and runtime manifest URI before restarting Dagster. | Not started. | No |
| OpenMetadata metadata seeding is imperative. | Domain, data-product, storage, Bronze container, and table stub metadata are seeded through script-driven OpenMetadata REST calls; table stubs can diverge from crawler-discovered metadata. | Add stale managed asset reconciliation and post-crawl validation; prefer native OpenMetadata ingestion once the relevant connector issues are fixed. | Not started. | No |
| OpenMetadata Superset registration used an incomplete local payload. | Resolved: the bootstrap registers the Superset dashboard service with the nested API connection that OpenMetadata's ingestion SDK expects. OpenMetadata still only shows Superset reports after its dashboard metadata ingestion pipeline crawls Superset. | Keep the schema-aligned Superset connection payload in the bootstrap and trigger/crawl Superset metadata after report artifacts are imported. | Fixed in bootstrap; verified manually through the OpenMetadata Superset service connection and imports. | Yes |
| Live end-to-end validation is mostly manual. | Static checks can pass while runtime behavior across Dagster, Floe, dbt, Trino, Superset, and OpenMetadata regresses. | Add a local e2e target mirroring the Azure flow: run all product pipelines, verify Silver and Gold table counts, verify each Gold mart has rows, verify Superset dashboards, and verify OpenMetadata domains/data products. | Azure e2e exists; local e2e target still needs implementation. | No |
| Floe OpenLineage fix is not verified in OpenLakeForge. | Floe v0.5.4 says the four OpenLineage emission bugs are fixed, but OpenLineage remains disabled in OpenLakeForge and no captured event run has proven OM can resolve Bronze-to-Silver lineage without a proxy. | Run the capture-based validation and, if it passes, re-enable only the Floe side of OpenLineage in a controlled test. | Run the [Floe OpenLineage capture test plan](testing/floe-openlineage-capture-test-plan.md). | No |

## Upstream-Related Debt

These items are caused by external project behavior or need upstream acceptance
before OpenLakeForge can remove its workaround cleanly.

| Weakness | Current Impact | Solution | Pending Work | Resolved |
| --- | --- | --- | --- | --- |
| `dbt-duckdb` + DuckDB Iceberg writes need custom schema handling and lack safe `CREATE OR REPLACE`. | Gold models use a custom `drop table if exists` then create materialization, which is non-atomic and can remove a working mart during failure. | Keep the custom materialization short term; add runtime regression tests; later remove the macro only if `dbt-duckdb` and DuckDB gain safe first-class Iceberg support. | Schema PR [duckdb/dbt-duckdb#755](https://github.com/duckdb/dbt-duckdb/pull/755) is open but received maintainer feedback against changing default schema behavior; keep the local macro for now. DuckDB Iceberg `CREATE OR REPLACE` was discussed in the community channel and is canceled for now because the REST API path does not allow a clean transaction. | No |
| Superset handling of Trino/Iceberg metadata fields breaks table preview. | Superset's Trino engine spec treats Trino Iceberg `$partitions` metadata fields as table partition columns and can generate invalid SQL Lab preview filters. | Upstream a Superset `TrinoEngineSpec` fix to ignore Iceberg `$partitions` metadata fields as real partition filters; once released, remove `patch_trino_iceberg_partitions.py` and retest preview through Trino. | PR [apache/superset#41055](https://github.com/apache/superset/pull/41055) is open and requires at least one approving review. | No |
| Superset still requires a custom image. | The stack cannot use the official Superset image directly because the local image installs Trino/PostgreSQL drivers and applies the Trino/Iceberg preview patch. | Remove the source patch after the upstream Superset fix; then decide whether drivers remain in a minimal project image, a chart-supported install path, or an official image variant. | Blocked by the Superset Trino/Iceberg patch being merged and released. | No |
| OpenLineage remains disabled in OpenMetadata. | OpenMetadata has catalog/governance metadata, but no trustworthy lineage graph. | Re-enable only after upstream fixes are verified: Floe event emission must resolve Bronze/Silver datasets, and `dbt-duckdb` must expose attached Iceberg catalog namespace/URI to `openlineage-dbt` for Silver/Gold datasets. | Floe side is fixed upstream and needs OpenLakeForge capture verification; dbt side remains blocked. | No |
| `openlineage-dbt` + `dbt-duckdb` resolves datasets under the DuckDB file namespace. | dbt lineage references `duckdb:///tmp/...` instead of the Polaris catalog namespace, so OpenMetadata cannot match Silver and Gold lineage to catalog entities. | Revisit the design in OpenLineage/dbt integration rather than patching only `dbt-duckdb`; then re-add `openlineage-dbt`, configure `OPENLINEAGE_*`, and route events directly to OpenMetadata. | PR [duckdb/dbt-duckdb#765](https://github.com/duckdb/dbt-duckdb/pull/765) was opened and closed; next work belongs in the OpenLineage repo/design discussion. | No |
| OpenMetadata Dagster connector is deferred. | The Dagster pipeline service and ingestion pipeline are registered but not triggered because the OpenMetadata connector uses the deprecated `PipelineRuns` GraphQL type while Dagster 1.x uses `Runs`. This is still present in OpenMetadata `1.13.0-release`, so bumping from `1.12.10` alone is not expected to fix it. | Open an upstream OpenMetadata issue or patch the connector to use the current Dagster GraphQL schema, then trigger the pre-registered ingestion pipeline and validate pipeline metadata. | Not started. | No |
| OpenMetadata Polaris OAuth handling needs a workaround. | The bootstrap job mints a Polaris bearer token and injects it into the Iceberg service connection because the connector dependency set does not handle the required Polaris OAuth client-credentials scope. | Upgrade or patch OpenMetadata/PyIceberg Polaris OAuth support so OpenMetadata can use the service principal credentials and scope directly. | Not started. | No |

## POC/Production Hardening Debt

These are OpenLakeForge-owned hardening items. They are separated from the
runtime bugs above because they mostly affect operational maturity.

| Weakness | Current Impact | Solution | Pending Work | Resolved |
| --- | --- | --- | --- | --- |
| Stateful services run in-cluster. | Local and Azure POC profiles run SeaweedFS, PostgreSQL, OpenSearch, Redis, Polaris, Dagster, OpenMetadata, Trino, and Superset inside Kubernetes without production-grade durability controls. | Replace stateful dependencies with managed or hardened services where appropriate: managed PostgreSQL, durable object storage, backups, resource limits, storage classes, and disaster recovery. | Not started. | No |
| Secrets and Terraform state are development-grade. | Local Terraform state and Kubernetes Secrets carry generated development credentials. | Use encrypted remote state, Key Vault or another external secret backend, External Secrets Operator, secret rotation, and reference-only Terraform outputs. | Not started. | No |
| Access is port-forward based. | Local and Azure POC access uses `kubectl port-forward` with no ingress, DNS, TLS, SSO, or public/private endpoint policy. | Add an access contract for ingress or private load balancers, DNS, TLS through cert-manager, and OIDC/SSO for Superset and OpenMetadata. | Not started. | No |
| Cloud provider adapters are still POC-level. | The Azure profile uses AKS and ACR but keeps SeaweedFS, Polaris, PostgreSQL, Kubernetes Secrets, and port-forward access. AWS/Glue/S3/RDS shapes are documented but not runnable. | Replace one provider dependency at a time behind the existing contracts, starting with managed PostgreSQL and object storage before changing the Iceberg catalog implementation. | Azure AKS/ACR POC exists; managed storage, managed database, and real access/secret adapters are still pending. | No |
| Local image distribution is fragile. | kind image loading, large image pulls, and corporate TLS interception can make first-run setup brittle. | Prefer registry-published images for non-local targets, pin heavy runtime image versions or digests, and document CA trust bootstrap for constrained networks. | Not started. | No |

## Verification Plans

- [Floe OpenLineage capture test plan](testing/floe-openlineage-capture-test-plan.md)

## References

- [ADR 0007: Superset Reporting over Gold Marts via Trino](adr/0007-superset-reporting-over-gold-via-trino.md)
- [ADR 0009: OpenMetadata Lineage Integration Deferred](adr/0009-openmetadata-lineage-direct-rest-push.md)
- [Floe issue #382](https://github.com/malon64/floe/issues/382)
- [Floe v0.5.4 release](https://github.com/malon64/floe/releases/tag/v0.5.4)
- [duckdb/dbt-duckdb#755](https://github.com/duckdb/dbt-duckdb/pull/755)
- [duckdb/dbt-duckdb#765](https://github.com/duckdb/dbt-duckdb/pull/765)
- [apache/superset#41055](https://github.com/apache/superset/pull/41055)
- [OpenMetadata 1.13 Dagster connector query still using PipelineRuns](https://raw.githubusercontent.com/open-metadata/OpenMetadata/1.13.0-release/ingestion/src/metadata/ingestion/source/pipeline/dagster/queries.py)
- [OpenMetadata 1.13 Superset service connection schema](https://raw.githubusercontent.com/open-metadata/OpenMetadata/1.13.0-release/openmetadata-spec/src/main/resources/json/schema/entity/services/connections/dashboard/supersetConnection.json)
- [OpenMetadata 1.13 Superset API credential schema](https://raw.githubusercontent.com/open-metadata/OpenMetadata/1.13.0-release/openmetadata-spec/src/main/resources/json/schema/entity/utils/supersetApiConnection.json)
