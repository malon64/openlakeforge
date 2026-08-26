# Architecture Decision Records

Architecture decision records capture decisions that shape OpenLakeForge.
ADRs are never edited after the fact — a later decision supersedes or amends
an earlier one and both stay on record, so this table is the fastest way to
see which ADRs are currently binding versus historical.

Start with `0001-v1-platform-baseline.md` for the v1 platform baseline,
`0008-two-phase-deploy-infra-and-artifacts.md` for the deploy-phase boundary,
`0026-medallion-ownership-and-catalog-namespace-contract.md` for the current
descriptor and medallion-ownership shape, and
`0028-python-owns-repository-orchestration.md` for why `olf` is the only
repository orchestration implementation.

| ADR | Decision | Status | Superseded / amended by |
| --- | --- | --- | --- |
| [0001](0001-v1-platform-baseline.md) | v1 platform baseline: Kubernetes-native, cloud-agnostic, batch-first, Iceberg table format | Binding | — |
| [0002](0002-local-object-storage-seaweedfs.md) | Use SeaweedFS for local S3-compatible object storage | Binding | — |
| [0003](0003-local-dagster-project-code-runtime.md) | Local Dagster runs domain code through one `project-code` runtime image | Binding | — |
| [0004](0004-manifest-first-floe-sales-ingestion.md) | Manifest-first Floe ingestion, generalized from the Sales seed product | Binding | — |
| [0005](0005-dbt-duckdb-gold-on-dagster-kubernetes.md) | dbt-duckdb Gold materialization on Dagster Kubernetes runs | Superseded | [0018](0018-trino-gold-materialization.md) (Gold now uses dbt-trino) |
| [0006](0006-openmetadata-governance-and-openlineage.md) | OpenMetadata governance with an OpenLineage normalisation proxy | Superseded | [0009](0009-openmetadata-lineage-direct-rest-push.md), then [0023](0023-native-openlineage-emission-restored.md) |
| [0007](0007-superset-reporting-over-gold-via-trino.md) | Superset reports over Gold marts via Trino, custom image | Binding | — |
| [0008](0008-two-phase-deploy-infra-and-artifacts.md) | Two-phase deploy: static platform (Phase 1) vs. dynamic artifacts (Phase 2) | Binding | Wrapper semantics amended by [0017](0017-shared-python-deploy-tooling.md), then [0028](0028-python-owns-repository-orchestration.md) |
| [0009](0009-openmetadata-lineage-direct-rest-push.md) | Defer OpenLineage integration; reject the proxy and a custom Dagster REST push | Superseded (lineage deferral only) | [0023](0023-native-openlineage-emission-restored.md) |
| [0010](0010-provider-contract-first-cloud-readiness.md) | Provider-neutral contracts prepare the local stack for cloud, before cloud is implemented | Binding | — |
| [0011](0011-iceberg-catalog-contract-allows-glue.md) | The Iceberg catalog contract describes the catalog implementation, allowing Glue as an alternative to Polaris | Binding | — |
| [0012](0012-contract-driven-provider-first-hardening.md) | Terraform typed contracts are the provider-boundary source of truth | Binding | — |
| [0013](0013-medallion-bucket-split.md) | Split the single `iceberg-data` bucket into per-layer Bronze/Silver/Gold buckets | Binding | — |
| [0014](0014-ops-artifact-bucket-and-domain-dagster-locations.md) | Rename the ops artifact bucket; one Dagster code location per domain | Amended | Code-location default superseded by [0019](0019-merged-dagster-code-location-default.md) |
| [0015](0015-aws-eks-managed-services-poc.md) | First AWS POC: EKS/ECR/S3/RDS/Glue behind existing contracts | Amended | Identity decision amended by [0016](0016-aws-eks-pod-identity-over-irsa.md) |
| [0016](0016-aws-eks-pod-identity-over-irsa.md) | Use EKS Pod Identity instead of IRSA (sandbox denies OIDC provider creation) | Binding | — |
| [0017](0017-shared-python-deploy-tooling.md) | Split shell (CLI orchestration) from Python (`tools/olf` cross-environment logic) | Superseded | [0025](0025-olf-owns-local-deployment-orchestration.md) (local), [0027](0027-olf-owns-cloud-deployment-orchestration.md) (AWS/Azure), fully by [0028](0028-python-owns-repository-orchestration.md) |
| [0018](0018-trino-gold-materialization.md) | Gold dbt models use Trino's Iceberg connector; drop DuckDB, Glue UDF, custom materialization | Binding | — |
| [0019](0019-merged-dagster-code-location-default.md) | One merged Dagster code location by default; per-domain split is an explicit opt-in | Binding | — |
| [0020](0020-polaris-relational-metastore.md) | Polaris uses the shared PostgreSQL relational metastore (local and Azure); AWS uses Glue | Binding | — |
| [0021](0021-domain-descriptor-v1alpha2-inventory.md) | Introduce the inventory-required `v1alpha2` domain descriptor shape | Superseded | [0026](0026-medallion-ownership-and-catalog-namespace-contract.md) (current shape is `v1alpha3` Lakehouse/Source) |
| [0022](0022-phase-two-catalog-namespace-reconciliation.md) | Move catalog namespace create/update/delete into Phase 2 (`olf catalog sync-namespaces`) | Binding | — |
| [0023](0023-native-openlineage-emission-restored.md) | Restore native OpenLineage emission from Floe and dbt-trino to OpenMetadata | Binding | — |
| [0024](0024-canonical-domain-model-package.md) | Create `openlakeforge-domain-model`, the single descriptor/inventory implementation shared by `olf` and project-code | Binding | — |
| [0025](0025-olf-owns-local-deployment-orchestration.md) | `olf`'s `DeploymentEngine`/`DeploymentProvider` owns the local (kind) lifecycle | Binding | — |
| [0026](0026-medallion-ownership-and-catalog-namespace-contract.md) | `lakehouse_code/` replaces `domains/`; Bronze source-owned, Silver domain-owned, Gold product-owned; flat catalog namespace contract | Binding | — |
| [0027](0027-olf-owns-cloud-deployment-orchestration.md) | `olf`'s `CloudProvider`/`CloudBackend` owns the AWS and Azure lifecycles | Binding | — |
| [0028](0028-python-owns-repository-orchestration.md) | `olf` is the only repository orchestration implementation; Make is deprecated checkout compatibility | Binding | — |
| [0029](0029-olf-owns-a-managed-toolchain.md) | `olf` downloads, verifies, and invokes its own versioned Terraform/Helm/kubectl/kind under `OLF_HOME` | Amended | `aws`/`az` CLI prerequisite removed by [0030](0030-sdk-managed-cloud-authentication.md) |
| [0030](0030-sdk-managed-cloud-authentication.md) | `olf` authenticates AWS and Azure through their SDKs directly; no `aws`/`az` CLI required | Binding | — |
| [0031](0031-pypi-embedded-platform-payload.md) | PyPI `openlakeforge` embeds a verified, immutable Terraform/Helm/runtime payload | Binding | — |
| [0032](0032-installed-project-root-and-transitional-projects.md) | An installed `olf` treats the current directory as the project root; `olf init --empty` projects are transitional until their first product | Binding | — |

## Other architecture references

- `../technical-debt.md` tracks known weaknesses, mitigations, and fix paths.
- `../testing/floe-openlineage-capture-test-plan.md` describes the capture-based
  validation path for Floe OpenLineage events.
