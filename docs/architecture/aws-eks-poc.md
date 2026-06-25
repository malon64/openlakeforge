# AWS EKS Managed Services POC

The AWS POC keeps the OpenLakeForge application shape from local and Azure, but
uses managed AWS services for the lakehouse dependencies that matter most for
operations cost and durability.

## Target Shape

| Boundary | AWS POC implementation |
| --- | --- |
| Cluster foundation | EKS, AWS VPC CNI, two public subnets, managed node group |
| Runtime images | ECR repositories for project-code and Superset |
| Object storage | S3 buckets for Bronze, Silver, Gold, and ops artifacts |
| Metadata database | RDS PostgreSQL for Dagster, OpenMetadata, and Superset |
| Iceberg catalog | AWS Glue Data Catalog through Glue Iceberg REST/SigV4 |
| Query path | Superset -> Trino -> Glue/S3 |
| Orchestration | Dagster Helm release with per-domain code locations |
| Artifacts | Floe manifests, reports, logs, and run artifacts in the S3 ops bucket |
| Identity | EKS Pod Identity associations for lakehouse workloads (see ADR 0016) |
| Access | `kubectl port-forward`, matching the other POCs |

Trino remains the implemented query engine. Athena is deferred because it would
change query pricing, Superset configuration, validation behavior, and the cost
model from always-on compute to pay-per-data-scanned queries.

## Terraform Roots

`infra/terraform/foundations/aws-eks` creates:

- VPC, internet gateway, route table, and two public subnets.
- EKS control plane and managed node group.
- EKS VPC CNI, CoreDNS, kube-proxy, EBS CSI, and Pod Identity agent add-ons.
- ECR repositories for project-code and Superset.
- An EKS Pod Identity role/association for the EBS CSI driver (see ADR 0016).

`infra/terraform/environments/aws-poc` consumes the foundation state and creates:

- S3 medallion and ops buckets.
- Glue product-layer databases for every Silver and Gold namespace.
- RDS PostgreSQL plus Kubernetes Secrets for application database users.
- A Pod Identity role/policy and per-service-account associations for S3 and Glue access.
- Trino, Dagster, Superset, and OpenMetadata through the shared Helm modules.

## Contracts

The AWS root exports the same `provider_contracts` output as local and Azure.
The active AWS contract implementations are:

- `foundation.eks`
- `platform.kubernetes.eks`
- `storage.aws_s3`
- `metadata_database.aws_rds_postgresql`
- `catalog.aws_glue`
- `artifacts.aws_ecr_and_s3`
- `identity.aws_pod_identity`

The Glue catalog contract uses `catalog_type = "glue"` and
`catalog_provider = "aws-glue"`. Consumers branch on those fields:

- Trino uses its Glue catalog configuration.
- Dagster passes AWS/Glue runtime environment to runs and code locations.
- Floe renders a Glue REST/SigV4 profile without S3-compatible endpoints or
  static object-storage secrets.
- dbt-duckdb uses an `aws_runtime` target with credential-chain S3 access and a
  Glue/SigV4 Iceberg attach.
- OpenMetadata registers a Glue-backed Iceberg service instead of Polaris OAuth.

## Workflow

Static checks:

```sh
make check-structure
make check-contracts
make check-infra
```

AWS POC lifecycle:

```sh
make aws-foundation-up
make aws-up
make aws-forward
make aws-e2e
make aws-down
make aws-foundation-down
```

`make aws-up` runs `aws-infra-up` and `aws-artifacts-deploy`. The artifact phase
generates Floe manifests, builds and pushes project-code to ECR, uploads Floe
manifests directly to the S3 ops bucket, imports Superset report assets, deploys
OpenMetadata metadata, patches Dagster runtime images, and restarts Dagster
workloads.

## Compatibility Gate

Before promoting the AWS POC beyond smoke validation, prove:

- one Silver write through Floe into Glue/S3;
- one Gold write through dbt-duckdb into Glue/S3;
- Trino can query both layers through Glue;
- OpenMetadata can seed and crawl Glue-backed namespaces.

The first AWS e2e target is a smoke test for cluster health, provider
contracts, S3, Glue, Trino, and core workloads. Full Dagster job execution and
dashboard/data quality assertions should be added after the Glue/S3 write path
is proven.

## POC Limits

The v1 AWS POC deliberately keeps local Terraform state, port-forward access,
Kubernetes Secrets, no ingress/TLS, no remote state, no Secrets Manager, and no
Lake Formation. Those belong behind later production-hardening contracts.
