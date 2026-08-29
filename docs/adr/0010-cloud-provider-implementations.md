# ADR 0010: Cloud provider implementations (AWS, Azure)

## Status

Binding. Both are proof-of-concept maturity; secure identity, ingress, and
network policy remain outside the active profile.

## Context

Local and Azure use Polaris while AWS uses Glue. v0.3 adds shared DEV, optional
UAT, and PROD stages in one cluster. Reusing a default Glue catalog with the
same unqualified database names would collide across stages or force a
provider-specific SQL naming scheme.

## Decision

Local and Azure retain a shared Polaris service with one physical catalog per
enabled stage. AWS uses one custom Glue catalog per enabled stage. The logical
SQL alias is always `lakehouse_<stage>`; owner/layer namespaces such as
`sales_silver` remain unchanged inside each catalog.

For AWS, the physical Glue catalog name is lower-case
`olf_<profile>_<stage>`. Long names are truncated with an eight-character hash
suffix. The physical Glue catalog ID is
`<account-id>:olf_<profile>_<stage>`. Trino exposes the catalog through its
native Glue adapter and its stage-specific `catalogid`, so
`lakehouse_dev.sales_silver.orders` and
`lakehouse_prod.sales_silver.orders` select different Glue catalogs while
keeping the same namespace/table convention.

AWS stage contracts carry Glue region, catalog ID, and workload-identity
reference, never credentials. Runtime access is stage-scoped: a stage runtime
receives only its storage, catalog alias, activation prefix, and identity
reference. Shared Trino may expose all stage catalogs, but its generated access
rules must bind each stage identity to its own alias when #114 provisions them.

AWS custom-catalog provisioning belongs to #114, not this decision's contract
implementation. It requires upgrading the AWS Terraform provider from the
current v5 line to a version supporting `aws_glue_catalog`. That upgrade must
be pinned and validated with the provisioning change.

EKS Pod Identity remains the AWS workload identity mechanism. Azure retains
AKS OIDC readiness, and local retains development-only Kubernetes Secrets.

### Existing provider substitutions remain binding

AWS continues to use EKS, S3 for Bronze/Silver/Gold/ops, RDS PostgreSQL, ECR,
and Pod Identity behind the existing capability interfaces. Azure continues to
use AKS and ACR while retaining SeaweedFS, Polaris, and in-cluster PostgreSQL.
Trino remains the shared query path; Athena is deferred because it would change
query cost, Superset connectivity, validation, and runtime contracts.

Polaris on local and Azure uses its dedicated persistent PostgreSQL role and
database. Its catalog state must survive a pod restart; an in-memory metastore
would lose catalogs, namespaces, principals, and tables. Polaris credentials
reach workloads through Kubernetes Secret references, never Terraform outputs
or rendered Helm values.

AWS Pod Identity replaces IRSA because the validation environment cannot create
the required IAM OIDC provider. Its contract remains
`identity.aws_pod_identity`; AWS storage and catalog authentication use the
stage workload-identity reference rather than static keys.

Account-specific tags, ownership, region, and cluster naming remain in ignored
`sandbox.tfvars` files or environment variables. All providers retain local
Terraform state, Kubernetes Secrets, port-forward access, no ingress/TLS, no
external secret manager, and no Lake Formation. Secure-state, private-network,
least-privilege, and external-secret work belongs to the secure beta profile.

## Consequences

The catalog technology remains Polaris for local/Azure and Glue for AWS; no
new Iceberg catalog technology is introduced. Physical Glue and S3 names stay
out of Deployment Profiles and `lakehouse_code` descriptors.

Stage resource provisioning, IAM policy implementation, External Secrets,
ingress, network policy, and live isolation testing are follow-on work in
#114, #133, and #155.

## History

2026-08-28: Rewritten for v3 stages. The former single AWS Glue catalog mapping
is replaced by a custom Glue catalog per stage so canonical SQL remains
portable.
