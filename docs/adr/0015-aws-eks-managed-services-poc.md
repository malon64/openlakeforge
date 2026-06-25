# ADR 0015: AWS EKS Managed Services POC

## Status

Accepted.

## Context

OpenLakeForge is contract-first. The local stack runs on kind with SeaweedFS,
in-cluster PostgreSQL, and Polaris. The Azure POC proves the same Helm-based
platform can run on AKS/ACR, but it intentionally keeps SeaweedFS, PostgreSQL,
and Polaris in the cluster.

The first AWS implementation should prove provider replacement behind the same
contracts instead of only moving Kubernetes hosting to EKS.

## Decision

Add an AWS POC profile with two Terraform phases:

- `infra/terraform/foundations/aws-eks` creates the VPC, public subnets, EKS
  cluster, managed node group, EKS add-ons, ECR repositories, and the IAM OIDC
  provider needed for IRSA.
- `infra/terraform/environments/aws-poc` consumes that foundation state, deploys
  the OpenLakeForge Helm services into EKS, and replaces selected dependencies
  with AWS managed services.

The active AWS POC contracts are:

- `storage.aws_s3` for Bronze, Silver, Gold, and ops buckets.
- `metadata_database.aws_rds_postgresql` for Dagster, OpenMetadata, and Superset
  metadata databases, with `ssl_mode=require`.
- `catalog.aws_glue` for product-layer Iceberg namespaces through AWS Glue.
- `artifacts.aws_ecr_and_s3` for runtime images and Floe/log/report artifacts.
- `identity.aws_irsa` for S3 and Glue runtime access.

Keep Trino as the first AWS query path. Athena remains a future adapter because
it changes query cost behavior, Superset connectivity, validation, and runtime
contracts.

## Consequences

The AWS POC reduces in-cluster stateful operations compared with Azure by moving
object storage, metadata PostgreSQL, and the Iceberg catalog to managed AWS
services. Dagster, Trino, Superset, OpenMetadata, Floe, and dbt-duckdb remain in
Kubernetes for POC parity.

This is not production hardening. Terraform state is local, access is still
port-forward based, Kubernetes Secrets still deliver generated passwords, and
there is no ingress, TLS, Secrets Manager, External Secrets, Lake Formation, or
remote state in v1.

The rollout is gated by AWS runtime compatibility: prove one Floe Silver write
and one dbt-duckdb Gold write through Glue/S3 before treating the AWS profile as
fully usable.
