# Provider Contracts

OpenLakeForge platform modules exchange provider-neutral contracts instead of
assuming one local implementation. Local, Azure, and AWS roots now publish the
same contract families while choosing different provider adapters.

This remains a POC boundary. It does not add Keycloak, Vault, remote Terraform
state, ingress/TLS, or production hardening services.

## Contract Source Of Truth

Terraform is the source of truth for provider contracts. The local, Azure, and
AWS platform roots normalize explicit contract objects in their `contracts.tf`
files and validate them with Terraform `check` blocks. Runtime scripts can read
the exported `provider_contracts` output and fall back to local defaults before
the stack is applied.

The current hardening is provider-first, not service-replacement-first. Dagster,
Trino, Superset, OpenMetadata, dbt-trino, and Floe remain the implemented v1
solution stack. Their runtime configuration should depend on provider contracts
for storage, catalog, secrets, artifacts, identity, and access.

## Phase Contract

OpenLakeForge has two deployment phases:

1. Cluster foundation creates or selects the Kubernetes cluster.
2. Platform apply deploys the lakehouse services into that cluster with
   Terraform, Helm, and Kubernetes resources.

Local implements the cluster foundation with `kind` through
`infra/terraform/foundations/local-kind`. Azure uses
`infra/terraform/foundations/azure-aks` for AKS and ACR. AWS uses
`infra/terraform/foundations/aws-eks` for VPC, EKS, node groups, add-ons, ECR,
and EKS Pod Identity readiness.

## Stable Contracts

| Contract | Local implementation | Azure POC implementation | AWS POC implementation |
| --- | --- | --- | --- |
| Foundation | `foundation.kind` | `foundation.aks` | `foundation.eks` |
| Kubernetes platform | `platform.kubernetes.kind` | `platform.kubernetes.aks` | `platform.kubernetes.eks` |
| Storage | `storage.s3_compatible.seaweedfs` | `storage.s3_compatible.seaweedfs_on_aks` | `storage.aws_s3` |
| Metadata database | `metadata_database.postgresql.in_cluster` | `metadata_database.postgresql.in_cluster_on_aks` | `metadata_database.aws_rds_postgresql` |
| Catalog | `catalog.iceberg_rest.polaris` | `catalog.iceberg_rest.polaris_on_aks` | `catalog.aws_glue` |
| Query | Trino Helm release | Trino Helm release | Trino Helm release with Glue catalog |
| Reporting | Superset Helm release | Superset Helm release with ACR image | Superset Helm release with ECR image and RDS metadata DB |
| Orchestration | Dagster with domain code locations | Dagster with ACR project-code image | Dagster with ECR project-code image and EKS Pod Identity |
| Artifacts | `artifacts.local_kind_and_s3` | `artifacts.azure_acr_and_s3_compatible_bucket` | `artifacts.aws_ecr_and_s3` |
| Secrets | `secrets.kubernetes_secret` | `secrets.kubernetes_secret_on_aks` | `secrets.kubernetes_secret_on_eks` |
| Identity | Local/basic app credentials | AKS OIDC readiness | `identity.aws_pod_identity` |
| Access | `kubectl port-forward` | `kubectl port-forward` | `kubectl port-forward` |
| Observability | `observability.object_log_archive` | `observability.object_log_archive_on_aks` | `observability.object_log_archive_on_eks` |

Consumers should depend on fields such as endpoint, bucket, region, Secret name,
database host, service name, image reference, access mode, and
`catalog_type`. They should not depend on whether the provider behind those
fields is SeaweedFS, S3, in-cluster PostgreSQL, RDS, kind, EKS, Polaris, or
Glue.

Product-owned runtime assets use logical aliases. Local and Azure resolve
`lakehouse_bronze` and `lakehouse_silver` to SeaweedFS-backed medallion buckets.
AWS resolves them to S3 buckets. Local and Azure resolve `iceberg_catalog` to
Polaris; AWS resolves it to Glue.

## Catalog Contract

The catalog contract describes an Iceberg catalog implementation. The local
provider sets:

```text
catalog_type = "rest"
catalog_provider = "polaris"
runtime_profile = "polaris-rest"
```

Polaris-specific fields such as REST URI, token URI, OAuth scope, and service
principal Secret names remain part of the local contract because local Floe,
dbt-trino, Trino, and OpenMetadata use them.

The AWS provider sets:

```text
catalog_type = "glue"
catalog_provider = "aws-glue"
runtime_profile = "aws-glue-rest"
```

The AWS implementation does not expose Polaris REST or OAuth credentials to
writer runtimes. Floe uses its native Glue catalog profile (`type: "glue"`).
Other consumers that support more than one Iceberg catalog implementation
branch on `catalog_type`. AWS keeps the same three-part SQL hierarchy as local
and Azure, but maps it onto Glue's two-level physical model: the first SQL
segment (`lakehouse_dev` by default) is the engine catalog alias for the AWS Glue
Data Catalog, while product layers such as `sales_order_revenue_silver` are Glue
databases/namespaces. A table resolves in SQL as
`lakehouse_dev.sales_order_revenue_silver.sales_order_score`.

## Local Defaults

The local provider profile remains intentionally lightweight:

- kind is the cluster foundation, managed by Terraform in a separate local
  foundation root.
- SeaweedFS implements the object storage contract.
- PostgreSQL runs in the cluster for Dagster, OpenMetadata, and Superset
  metadata.
- Kubernetes Secrets are the secret delivery mechanism.
- Basic/local application credentials are development-only.
- `kubectl port-forward` is the access model.
- Terraform state remains local and may contain development credentials.

These defaults are not production controls. They are the local implementation of
the same contracts a future provider profile should satisfy.

## AWS POC Notes

The AWS roots use managed S3, RDS PostgreSQL, Glue, ECR, and EKS Pod Identity, but keep the
same POC limits as the other environments: local Terraform state, Kubernetes
Secrets, no ingress/TLS, and port-forward access. Athena, Secrets Manager,
External Secrets, Lake Formation, remote state, and production observability are
future adapters, not part of the active AWS POC.
