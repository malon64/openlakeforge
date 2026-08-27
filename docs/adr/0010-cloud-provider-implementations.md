# ADR 0010: Cloud provider implementations (AWS, Azure)

## Status

Binding. Both are proof-of-concept maturity — see "POC limits" below.

## Context

Provider contracts (ADR 0003) only prove portability if something other than the
local stack satisfies them. Two cloud targets exist, and they deliberately test
different things.

## Decision

### AWS replaces dependencies; Azure relocates hosting

**AWS (`aws-poc`)** substitutes managed services behind the existing contracts:

| Contract | AWS implementation |
| --- | --- |
| `foundation` | EKS, VPC with two public subnets, managed node group, VPC CNI / CoreDNS / kube-proxy / EBS CSI / Pod Identity add-ons |
| `artifact_registry` | ECR |
| `storage` | S3 for Bronze, Silver, Gold, ops |
| `metadata_database` | RDS PostgreSQL (`ssl_mode=require`) |
| `catalog` | AWS Glue Data Catalog |
| `identity` | EKS Pod Identity |

**Azure (`azure-poc`)** keeps SeaweedFS, Polaris, and in-cluster PostgreSQL, and
replaces only the Kubernetes and registry hosting with AKS and ACR.

The split is intentional. AWS proves that *replacing a contract implementation*
works — Glue instead of Polaris, S3 instead of SeaweedFS. Azure proves the
*Helm-based platform itself* runs on a managed cluster. Doing both at once on one
provider would leave neither claim isolated when something failed.

Trino stays the query path on both. Athena is deferred: it changes query cost
from always-on compute to pay-per-scan, and with it Superset connectivity,
validation behaviour, and the runtime contracts.

### Polaris for local and Azure; Glue for AWS

Polaris owns Iceberg table identity on local and Azure, backed by a dedicated
`polaris` role and database in the in-cluster PostgreSQL.

The relational metastore is not optional. Polaris's in-memory metastore lost
catalogs, namespaces, principals, and tables on every pod restart, and recovery
meant a full platform re-apply that could rotate client credentials while Trino
still held the old ones. For a small team on a small cluster, a restart is a
routine event, so losing catalog state to one is unrecoverable in practice.

Credentials reach Polaris only through a `postgresql-polaris-creds` Kubernetes
Secret; the contract exposes the Secret name, never a JDBC string or password, so
no credential appears in Terraform outputs or rendered Helm values.

AWS is deliberately excluded: its catalog contract is implemented by Glue and
must not acquire a Polaris dependency.

### EKS Pod Identity instead of IRSA

Workload identity uses EKS Pod Identity. Service accounts bind to roles through
`aws_eks_pod_identity_association`; there is no OIDC provider, issuer URL, or
`sub`/`aud` trust condition.

IRSA was the original choice and is not available: it requires an IAM OIDC
identity provider created from the cluster issuer, and the validation sandbox
denies `iam:CreateOpenIDConnectProvider` outright. Its guardrails also mandate a
`limited-` name prefix, and an OIDC provider has no name to apply one to.

Pod Identity is the better fit regardless — it removes the OIDC provider as a
resource to manage and keeps the per-service-account role model.

The identity contract is `identity.aws_pod_identity`
(`workload_identity = "aws-pod-identity"`, `oidc_enabled = false`); storage and
catalog auth modes are `aws-pod-identity` / `aws-sigv4-pod-identity`.

### Account-specific configuration stays local

Tags, owner, region, and cluster naming live in gitignored `sandbox.tfvars`
files created from tracked `.example` templates, or in environment variables.
Nothing account-specific is committed.

## POC limits

Both targets keep local Terraform state, Kubernetes Secrets, `kubectl
port-forward` access, no ingress or TLS, no external secret manager, and — on
AWS — no Lake Formation. Remote encrypted state, private subnets, least-privilege
per-service roles, External Secrets, ingress with cert-manager, and OIDC access
control belong to the secure beta profile.

`poc` names the environment these were validated in. It does not mean a reduced
OpenLakeForge stack: both run the same platform as local.

## History

Merges the decisions previously recorded as ADR 0015 (the AWS EKS managed-services
POC), 0016 (EKS Pod Identity over IRSA, which amended 0015's identity choice), and
0020 (the Polaris relational metastore). The Azure target was previously described
only in `docs/architecture/azure-aks-poc.md`.
