# Architecture Decision Records

Architecture decision records capture decisions that shape OpenLakeForge and should remain stable unless a later ADR supersedes them.

Start with `0001-v1-platform-baseline.md` for the v1 platform baseline.

`0002-local-object-storage-seaweedfs.md` supersedes the Iteration 1 local object
storage choice from Garage to SeaweedFS.

`0003-local-dagster-project-code-runtime.md` records the Iteration 2 Dagster and
project-code runtime boundary.

`0004-manifest-first-floe-sales-ingestion.md` records the Iteration 3 Sales dlt
and Floe manifest-first runtime boundary.

`0005-dbt-duckdb-gold-on-dagster-kubernetes.md` records the Iteration 4
dbt-duckdb Gold runtime boundary.

`0006-openmetadata-governance-and-openlineage.md` records the Iteration 5
OpenMetadata deployment, the OpenLineage proxy normalisation pattern, and the
Polaris bootstrap workaround.

`0007-superset-reporting-over-gold-via-trino.md` records the Iteration 6
Superset deployment model, custom image, and YAML-based report bundle lifecycle.

`0008-two-phase-deploy-infra-and-artifacts.md` records the split between
Terraform-owned static infrastructure (Phase 1) and domain artifact deployment
(Phase 2), and defines the CD boundary.

`0009-openmetadata-lineage-direct-rest-push.md` supersedes ADR 0006. It
documents the upstream bugs in Floe and dbt-duckdb that prevent reliable
OpenLineage emission, explains why both the proxy and a custom Dagster REST
push were abandoned, and records the decision to defer all lineage integration
until upstream connectors are fixed.

`0010-provider-contract-first-cloud-readiness.md` records the decision to make
the local stack cloud-ready through provider-neutral contracts while keeping
local as the only implemented environment for now.

`0011-iceberg-catalog-contract-allows-glue.md` records the decision that the
catalog contract describes the Iceberg catalog implementation, allowing a future
AWS provider profile to use Glue instead of self-hosted Polaris.

`0012-contract-driven-provider-first-hardening.md` records the decision to make
Terraform typed contracts the provider boundary source of truth while keeping
the current v1 services as the implemented solution stack.

`0014-ops-artifact-bucket-and-domain-dagster-locations.md` records the
`openlakeforge-ops` artifact bucket rename, object-backed log/report archive,
and per-domain Dagster code locations.

`0015-aws-eks-managed-services-poc.md` records the first AWS POC provider
implementation: EKS/ECR plus S3, RDS PostgreSQL, Glue, and IRSA behind the
existing OpenLakeForge contracts.

`0016-aws-eks-pod-identity-over-irsa.md` amends 0015 to use EKS Pod Identity
instead of IRSA, because the lab sandbox denies `iam:CreateOpenIDConnectProvider`;
it also moves sandbox naming/tags into `.tfvars` files.
