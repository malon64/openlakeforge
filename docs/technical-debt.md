# OpenLakeForge Technical Debt

This document tracks known OpenLakeForge weaknesses, current mitigations, and
the intended fix path. ADRs remain the historical decision log; this file is the
current technical debt backlog.

`Resolved` means resolved in OpenLakeForge, not merely fixed upstream. `Yes
(branch)` means the implementation and local migration validation are complete
on the current branch; its listed cloud acceptance work still has to pass before
the change is merged.

## OpenLakeForge-Owned Debt

These items can be fixed primarily in this repository without waiting for an
upstream project to change behavior.

| Weakness | Current Impact | Solution | Pending Work | Resolved |
| --- | --- | --- | --- | --- |
| Dynamic artifact revision drift is still possible outside the deploy flow. | The local and Azure artifact deploy flows now generate Floe manifests before building the `project-code` image, upload the same files, and run Dagster in explicit remote manifest mode. Manual image builds, manual bucket uploads, or interrupted deploys can still make Dagster load one manifest revision while Floe runners execute another. | Enforce one artifact revision contract across the image label, Dagster environment, uploaded manifest hash, and runtime manifest URI before restarting Dagster. | Add hash stamping and validation after the remote/local manifest mode cleanup. | No |
| OpenMetadata metadata seeding is imperative. | Domain, data-product, medallion bucket containers, and table stub metadata are seeded through script-driven OpenMetadata REST calls; table stubs can diverge from crawler-discovered metadata. | Add stale managed asset reconciliation and post-crawl validation; prefer native OpenMetadata ingestion once the relevant connector issues are fixed. | Not started. | No |
| OpenMetadata Superset registration used an incomplete local payload. | Resolved: the bootstrap registers the Superset dashboard service with the nested API connection that OpenMetadata's ingestion SDK expects. OpenMetadata still only shows Superset reports after its dashboard metadata ingestion pipeline crawls Superset. | Keep the schema-aligned Superset connection payload in the bootstrap and trigger/crawl Superset metadata after report artifacts are imported. | Fixed in bootstrap; verified manually through the OpenMetadata Superset service connection and imports. | Yes |
| E2E readiness is weaker than the runtime it validates. | `make local-e2e` can pass pod readiness while Dagster product gRPC locations are still unavailable, then fail a launch with GraphQL 500. Historical failed Dagster-run pods can also block preflight despite being unrelated to the current suite. | Gate on loadable Dagster repositories, retry bounded transient launch failures, and distinguish historical Jobs from jobs created by the suite. | Tracked in [#61](https://github.com/malon64/openlakeforge/issues/61); preserve diagnostics and tests for persistent failures. | No |
| Live end-to-end validation is mostly manual. | Static checks can pass while runtime behavior across Dagster, Floe, dbt, Trino, Superset, and OpenMetadata regresses. | Keep all environments on the shared full e2e suite: run all product pipelines, verify Silver and Gold table counts, verify each Gold mart has rows, verify Superset dashboards, and verify OpenMetadata domains/data products. | Local, Azure, and AWS now default to the shared full suite; AWS retains `--suite smoke` for explicit preflight-only validation. Automating the live suite remains pending. | No |
| Validation and e2e scripts remain bash and grep-assert on script text. | ADR 0017 moved the deploy pipeline's cross-environment logic into `tools/olf`; the Python-module invariants formerly grepped by `check-contracts.sh` are covered by behavioral pytest tests, and e2e behavior now lives in `olf e2e run`. Some `scripts/test/check-*.sh` validation remains shell. | Rewrite remaining `check-*` logic that asserts behavior into `tools/olf` tests or commands while leaving true filesystem/Terraform/Helm structure checks in shell. | Python-module source-grep assertions migrated to `tools/olf/tests`; e2e migrated to `olf`; remaining work is shell check cleanup. | No |

## Upstream-Related Debt

These items are caused by external project behavior or need upstream acceptance
before OpenLakeForge can remove its workaround cleanly.

| Weakness | Current Impact | Solution | Pending Work | Resolved |
| --- | --- | --- | --- | --- |
| DuckDB Gold replacement was non-atomic. | Resolved on this branch: dbt-trino uses Trino Iceberg `CREATE OR REPLACE TABLE` with `on_table_exists: replace`, preserving the previous snapshot on failed replacement. | Keep the Trino atomic-replacement regression test in the migration gate. | Local validation passed; validate against AWS Trino before merge. | Yes (branch) |
| DuckDB Glue location/UDF/orphan workaround. | Resolved on this branch by routing all Gold writes through the existing Trino Glue Iceberg connector; DuckDB plugin, NumPy dependency, direct Glue REST creation, and custom UDF are removed. | Keep AWS Glue location and referenced-file inventory checks in the migration gate. | Local validation passed; validate the full AWS product suite before merge. | Yes (branch) |
| Superset handling of Trino/Iceberg metadata fields breaks table preview. | Superset's Trino engine spec treats Trino Iceberg `$partitions` metadata fields as table partition columns and can generate invalid SQL Lab preview filters. | Upstream a Superset `TrinoEngineSpec` fix to ignore Iceberg `$partitions` metadata fields as real partition filters; once released, remove `patch_trino_iceberg_partitions.py` and retest preview through Trino. | PR [apache/superset#41055](https://github.com/apache/superset/pull/41055) is open and requires at least one approving review. | No |
| Superset still requires a custom image. | The stack cannot use the official Superset image directly because the local image installs Trino/PostgreSQL drivers and applies the Trino/Iceberg preview patch. | Remove the source patch after the upstream Superset fix; then decide whether drivers remain in a minimal project image, a chart-supported install path, or an official image variant. | Blocked by the Superset Trino/Iceberg patch being merged and released. | No |
| Floe manifest replay omitted dataset context from OpenLineage events. | Resolved: Floe 0.6.11 passes manifest entities to the runtime lineage observer, while OpenLakeForge emits resolved source URIs and supplies the OpenMetadata JWT only through the runner's Kubernetes Secret reference. OpenMetadata now resolves the complete Bronze→Silver→Gold graph. | Keep Floe at 0.6.11 or later, retain `resolved-uri` manifest generation, and keep credentials out of generated manifests. | Fixed by [malon64/floe#455](https://github.com/malon64/floe/issues/455) and released in [Floe 0.6.11](https://github.com/malon64/floe/releases/tag/v0.6.11). Local acceptance verified all 15 Bronze→Silver edges and complete upstream graphs for all 9 Gold marts. | Yes |
| `openlineage-dbt` + `dbt-duckdb` resolves datasets under the DuckDB file namespace. | Resolved for Gold on this branch: dbt-trino emits `trino://host:port` dataset namespaces that map to the canonical Iceberg service. | Keep the captured event and OpenMetadata entity-resolution test. | Local validation passed; include AWS OpenMetadata entity resolution in the merge gate. | Yes (branch) |
| OpenMetadata Dagster connector is deferred. | The Dagster pipeline service and ingestion pipeline are registered but not triggered because the OpenMetadata connector uses the deprecated `PipelineRuns` GraphQL type while Dagster 1.x uses `Runs`. This is still present in OpenMetadata `1.13.0-release`, so bumping from `1.12.10` alone is not expected to fix it. | Open an upstream OpenMetadata issue or patch the connector to use the current Dagster GraphQL schema, then trigger the pre-registered ingestion pipeline and validate pipeline metadata. | Not started. | No |
| OpenMetadata Polaris OAuth handling needs a workaround. | The bootstrap job mints a Polaris bearer token and injects it into the Iceberg service connection because the connector dependency set does not handle the required Polaris OAuth client-credentials scope. | Upgrade or patch OpenMetadata/PyIceberg Polaris OAuth support so OpenMetadata can use the service principal credentials and scope directly. | Not started. | No |
| Floe manifest replay did not preserve S3 storage definitions for AWS runs. | Resolved: OpenLakeForge uses Floe-generated manifests and no longer rewrites them to run with `-c <config-uri> -p <profile-uri>`. AWS artifact deployment publishes only Floe-generated manifests. | Keep manifest generation owned by Floe; do not add post-generation JSON mutation in OpenLakeForge. | Fixed by [malon64/floe#425](https://github.com/malon64/floe/issues/425) and consumed through the configured Floe runner image. | Yes |
| Floe Iceberg S3 writes did not use EKS Pod Identity container credentials. | Resolved upstream; OpenLakeForge now points AWS runner manifests at the configured Floe image. | Continue using EKS Pod Identity for AWS Floe runner pods. | Fixed by [malon64/floe#426](https://github.com/malon64/floe/issues/426). AWS end-to-end validation remains tracked under the rollout gate below. | Yes |

## POC/Production Hardening Debt

These are OpenLakeForge-owned hardening items. They are separated from the
runtime bugs above because they mostly affect operational maturity.

| Weakness | Current Impact | Solution | Pending Work | Resolved |
| --- | --- | --- | --- | --- |
| Stateful services run in-cluster. | Local and Azure POC profiles run SeaweedFS, PostgreSQL, OpenSearch, Redis, Polaris, Dagster, OpenMetadata, Trino, and Superset inside Kubernetes without production-grade durability controls. | Replace stateful dependencies with managed or hardened services where appropriate: managed PostgreSQL, durable object storage, backups, resource limits, storage classes, and disaster recovery. | Not started. | No |
| Secrets and Terraform state are development-grade. | Local Terraform state and Kubernetes Secrets carry generated development credentials. | Use encrypted remote state, Key Vault or another external secret backend, External Secrets Operator, secret rotation, and reference-only Terraform outputs. | Not started. | No |
| Access is port-forward based. | Local and Azure POC access uses `kubectl port-forward` with no ingress, DNS, TLS, SSO, or public/private endpoint policy. | Add an access contract for ingress or private load balancers, DNS, TLS through cert-manager, and OIDC/SSO for Superset and OpenMetadata. | Not started. | No |
| SeaweedFS built-in UIs are local-dev only. | The Filer and Master UIs help inspect local object storage but are exposed only by localhost port-forward and are not secured by ingress, TLS, SSO, or fine-grained read-only policy. | Keep them as local inspection tools; for shared environments, add proper identity/access policy or replace them with provider-native object storage console access. | Local port-forward support implemented; production access model deferred. | No |
| Full observability stack is deferred. | Logs, Floe reports, dbt artifacts, and Dagster compute logs are archived to `openlakeforge-ops`, but there is no Loki/Grafana query UI, Prometheus metrics, tracing, or alerting. | Keep `observability.object_log_archive` as the hardened processing baseline, then add Loki/Grafana or provider-native logging behind a future observability adapter. | Object archive implemented; query UI/metrics/tracing not started. | No |
| Floe profile variables and remote path resolution needed rendered AWS configs. | Resolved: product Floe configs now use profile variables for bucket and region values, and OpenLakeForge removed the local config renderer. | Keep provider-specific values in Floe profiles and let Floe render manifests from config/profile inputs. | Fixed by [malon64/floe#424](https://github.com/malon64/floe/issues/424) and consumed through the configured Floe runner image. | Yes |
| AWS Glue/S3 writer compatibility is a rollout gate. | The AWS profile now provisions EKS/ECR, S3, RDS PostgreSQL, Glue, and EKS Pod Identity, but Floe and dbt-trino writes through Glue/S3 still need live proof before the AWS POC is considered complete. | Keep Trino as the first AWS query path and run a focused compatibility test: one Floe Silver write and one dbt-trino Gold write into Glue/S3, then query both through Trino. | Floe Silver write+read through Glue has been proven. Pending: a green Dagster dbt-trino Gold run, Trino query of both layers, Gold replacement/referenced-file checks, and OpenMetadata crawl/entity resolution. | No |
| Cloud provider adapters are still POC-level. | Azure uses AKS and ACR but keeps SeaweedFS, Polaris, PostgreSQL, Kubernetes Secrets, and port-forward access. AWS replaces storage, metadata PostgreSQL, and catalog with managed services, but still uses local state, Kubernetes Secrets, port-forward access, and no Lake Formation. | Promote each provider dependency behind explicit contracts: remote state, external secret managers, workload identity-only app auth, ingress/private access, TLS, backups, and observability. | Azure managed services and AWS production hardening are still pending. | No |
| Local image distribution is fragile. | kind image loading, large image pulls, and corporate TLS interception can make first-run setup brittle. | Prefer registry-published images for non-local targets, pin heavy runtime image versions or digests, and document CA trust bootstrap for constrained networks. | Not started. | No |

## Verification Plans

- [Floe OpenLineage capture test plan](testing/floe-openlineage-capture-test-plan.md)

## References

- [ADR 0007: Superset Reporting over Gold Marts via Trino](adr/0007-superset-reporting-over-gold-via-trino.md)
- [ADR 0009: OpenMetadata Lineage Integration Deferred](adr/0009-openmetadata-lineage-direct-rest-push.md)
- [Floe issue #382](https://github.com/malon64/floe/issues/382)
- [Floe issue #424](https://github.com/malon64/floe/issues/424)
- [Floe issue #425](https://github.com/malon64/floe/issues/425)
- [Floe issue #426](https://github.com/malon64/floe/issues/426)
- [OpenLakeForge issue #61: Harden local e2e readiness and Dagster launch retries](https://github.com/malon64/openlakeforge/issues/61)
- [Floe releases](https://github.com/malon64/floe/releases)
- [duckdb/dbt-duckdb#755](https://github.com/duckdb/dbt-duckdb/pull/755)
- [duckdb/dbt-duckdb#765](https://github.com/duckdb/dbt-duckdb/pull/765)
- [apache/superset#41055](https://github.com/apache/superset/pull/41055)
- [OpenMetadata 1.13 Dagster connector query still using PipelineRuns](https://raw.githubusercontent.com/open-metadata/OpenMetadata/1.13.0-release/ingestion/src/metadata/ingestion/source/pipeline/dagster/queries.py)
- [OpenMetadata 1.13 Superset service connection schema](https://raw.githubusercontent.com/open-metadata/OpenMetadata/1.13.0-release/openmetadata-spec/src/main/resources/json/schema/entity/services/connections/dashboard/supersetConnection.json)
- [OpenMetadata 1.13 Superset API credential schema](https://raw.githubusercontent.com/open-metadata/OpenMetadata/1.13.0-release/openmetadata-spec/src/main/resources/json/schema/entity/utils/supersetApiConnection.json)
