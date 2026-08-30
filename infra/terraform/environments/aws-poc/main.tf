terraform {
  # >= 1.7.0 for the `removed` block in modules/catalog/aws-glue (ADR 0002):
  # Glue database lifecycle moved to Phase 2, and `removed` is how existing
  # deployments hand those databases to olf without Terraform destroying them.
  required_version = ">= 1.7.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.62"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 3.1"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.36"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.7"
    }
  }
}

provider "aws" {
  region = local.aws_region

  default_tags {
    tags = var.default_tags
  }
}

provider "kubernetes" {
  config_path    = local.kubeconfig_path
  config_context = local.kubernetes_platform_contract.kube_context
}

provider "helm" {
  repository_cache       = local.helm_repository_cache_path
  repository_config_path = local.helm_repository_config_path

  kubernetes = {
    config_path    = local.kubeconfig_path
    config_context = local.kubernetes_platform_contract.kube_context
  }
}

locals {
  aws_region      = coalesce(try(local.foundation_contract.aws_region, null), var.aws_region)
  kubeconfig_path = var.kubeconfig_path != null ? abspath(pathexpand(var.kubeconfig_path)) : coalesce(try(local.foundation_contract.kubeconfig_path, null), abspath("${path.root}/../../../../.tmp/kubeconfigs/aws.yaml"))
  helm_repository_cache_path = (
    var.helm_repository_cache_path != null
    ? abspath(pathexpand(var.helm_repository_cache_path))
    : abspath("${path.root}/../../../../.tmp/helm/aws/repository-cache")
  )
  helm_repository_config_path = (
    var.helm_repository_config_path != null
    ? abspath(pathexpand(var.helm_repository_config_path))
    : abspath("${path.root}/../../../../.tmp/helm/aws/repositories.yaml")
  )
  artifact_base_uri      = "s3://${local.storage_contract.ops_bucket_name}"
  floe_manifest_base_uri = "${local.artifact_base_uri}/floe/manifests"
  floe_report_base_uri   = "${local.artifact_base_uri}/floe/reports"
  log_base_uri           = "${local.artifact_base_uri}/logs"
  run_artifact_base_uri  = "${local.artifact_base_uri}/run-artifacts"
  domain_floe_manifest_uris = {
    sales        = "${local.floe_manifest_base_uri}/sales/sales.manifest.json"
    supply_chain = "${local.floe_manifest_base_uri}/supply_chain/supply_chain.manifest.json"
  }
  # Databases themselves are not declared here. `olf catalog sync-namespaces`
  # reconciles them from the lakehouse inventory during artifacts-deploy (ADR
  # 0022); this root only records which naming model those databases follow.
  catalog_namespace_model = "medallion-owner"
  # Service accounts bound to the lakehouse workload role via EKS Pod Identity
  # associations (not IRSA). No SA annotation is required with Pod Identity.
  workload_service_accounts = [
    "dagster",
    "dagster-dagster-user-deployments-user-deployments",
    "trino",
    "openmetadata",
    "openmetadata-bootstrap",
  ]
  service_account_annotations = {}
}

resource "kubernetes_namespace_v1" "lakehouse" {
  metadata {
    name = var.namespace
  }
}

# EKS does not ship a default StorageClass (kind does), so PVCs created without an
# explicit class never bind. Provide a gp3 default backed by the EBS CSI driver
# (which authenticates via Pod Identity). WaitForFirstConsumer provisions the
# volume in the consuming pod's availability zone.
resource "kubernetes_storage_class_v1" "gp3" {
  metadata {
    name = "gp3"
    annotations = {
      "storageclass.kubernetes.io/is-default-class" = "true"
    }
  }

  storage_provisioner    = "ebs.csi.aws.com"
  volume_binding_mode    = "WaitForFirstConsumer"
  allow_volume_expansion = true

  parameters = {
    type = "gp3"
  }
}

module "s3" {
  source = "../../modules/storage/aws-s3"

  bucket_name_prefix = var.bucket_name_prefix
  region             = local.aws_region
  bronze_bucket_name = var.bronze_bucket_name
  silver_bucket_name = var.silver_bucket_name
  gold_bucket_name   = var.gold_bucket_name
  ops_bucket_name    = var.ops_bucket_name
}

module "glue" {
  source = "../../modules/catalog/aws-glue"

  region       = local.aws_region
  account_id   = local.foundation_contract.aws_account_id
  catalog_name = var.catalog_name
}

module "rds_postgresql" {
  source = "../../modules/storage/rds-postgresql"

  namespace           = kubernetes_namespace_v1.lakehouse.metadata[0].name
  name_prefix         = "openlakeforge"
  vpc_id              = local.foundation_contract.vpc_id
  subnet_ids          = local.foundation_contract.subnet_ids
  allowed_cidr_blocks = [local.foundation_contract.vpc_cidr_block]
  instance_class      = var.rds_instance_class
  enable_openmetadata = var.enable_governance
  enable_superset     = var.enable_analytics

  depends_on = [
    kubernetes_namespace_v1.lakehouse,
  ]
}

resource "aws_iam_policy" "lakehouse_workloads" {
  name        = "${local.foundation_contract.cluster_name}-openlakeforge-workloads"
  description = "OpenLakeForge AWS POC workload access to S3 and Glue"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:ListBucket",
          "s3:GetBucketLocation",
          # Bucket-scoped: ListMultipartUploads only matches the bucket ARN, not
          # object ARNs, so it must live with the other bucket-level actions.
          "s3:ListBucketMultipartUploads",
        ]
        Resource = values(module.s3.bucket_arns)
      },
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:AbortMultipartUpload",
          "s3:ListMultipartUploadParts",
        ]
        Resource = [
          for arn in values(module.s3.bucket_arns) : "${arn}/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "glue:GetCatalog",
          "glue:GetDatabase",
          "glue:GetDatabases",
          "glue:CreateDatabase",
          "glue:UpdateDatabase",
          "glue:DeleteDatabase",
          "glue:GetTable",
          "glue:GetTables",
          "glue:CreateTable",
          "glue:UpdateTable",
          "glue:DeleteTable",
          "glue:GetPartition",
          "glue:GetPartitions",
          "glue:BatchGetPartition",
          "glue:CreatePartition",
          "glue:UpdatePartition",
          "glue:DeletePartition",
        ]
        Resource = "*"
      },
    ]
  })
}

resource "aws_iam_role" "lakehouse_workloads" {
  name = "${local.foundation_contract.cluster_name}-openlakeforge-workloads"

  # EKS Pod Identity trust: the Pod Identity agent assumes this role on behalf of
  # the associated service accounts (see aws_eks_pod_identity_association below).
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "pods.eks.amazonaws.com" }
        Action = [
          "sts:AssumeRole",
          "sts:TagSession",
        ]
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lakehouse_workloads" {
  role       = aws_iam_role.lakehouse_workloads.name
  policy_arn = aws_iam_policy.lakehouse_workloads.arn
}

resource "aws_eks_pod_identity_association" "lakehouse_workloads" {
  for_each = toset(local.workload_service_accounts)

  cluster_name    = local.foundation_contract.cluster_name
  namespace       = var.namespace
  service_account = each.value
  role_arn        = aws_iam_role.lakehouse_workloads.arn
}

module "trino" {
  source = "../../modules/query/trino"

  namespace                   = kubernetes_namespace_v1.lakehouse.metadata[0].name
  base_values_file            = "${path.root}/../../../helm/values/local/trino.yaml"
  chart_package_path          = var.trino_chart_package_path
  storage_contract            = local.storage_contract
  catalog_contract            = local.catalog_contract
  catalog_bootstrap_revision  = "aws-glue"
  service_account_annotations = local.service_account_annotations
  # EKS Pod Identity binds the S3/Glue workload role to the "trino" service account
  # (see aws_eks_pod_identity_association.lakehouse_workloads). Force the Trino chart
  # to create and run under that SA; annotations stay empty under Pod Identity.
  service_account_name = "trino"

  depends_on = [
    module.glue,
    aws_iam_role_policy_attachment.lakehouse_workloads,
    aws_eks_pod_identity_association.lakehouse_workloads,
  ]
}

module "openmetadata" {
  source = "../../modules/governance/openmetadata"
  count  = var.enable_governance ? 1 : 0

  namespace               = kubernetes_namespace_v1.lakehouse.metadata[0].name
  base_values_file        = "${path.root}/../../../helm/values/local/openmetadata.yaml"
  deps_values_file        = "${path.root}/../../../helm/values/local/openmetadata-deps.yaml"
  chart_package_path      = var.openmetadata_chart_package_path
  deps_chart_package_path = var.openmetadata_deps_chart_package_path
  catalog_contract        = local.catalog_contract
  storage_contract        = local.storage_contract
  postgresql_contract     = local.metadata_database_contract
  postgresql_ssl_mode     = "require"
  # Empty by design: the database schemas mirror Glue databases, which now
  # come into existence in Phase 2. `olf openmetadata deploy-metadata` creates
  # each databaseSchema entity right before it seeds that schema's tables.
  catalog_schema_names        = []
  catalog_database_name       = var.catalog_name
  catalog_refresh_enabled     = false
  service_account_annotations = local.service_account_annotations

  depends_on = [
    module.glue,
    module.rds_postgresql,
    aws_eks_pod_identity_association.lakehouse_workloads,
  ]
}

module "superset" {
  source = "../../modules/analytics/superset"
  count  = var.enable_analytics ? 1 : 0

  namespace           = kubernetes_namespace_v1.lakehouse.metadata[0].name
  base_values_file    = "${path.root}/../../../helm/values/local/superset.yaml"
  chart_package_path  = var.superset_chart_package_path
  image_repository    = var.superset_image_repository
  image_tag           = var.superset_image_tag
  image_pull_policy   = var.superset_image_pull_policy
  postgresql_contract = local.metadata_database_contract
  postgresql_ssl_mode = "require"
  depends_on = [
    module.rds_postgresql,
    module.trino,
  ]
}

moved {
  from = module.openmetadata
  to   = module.openmetadata[0]
}

moved {
  from = module.superset
  to   = module.superset[0]
}

module "dagster" {
  source = "../../modules/orchestration/dagster"

  namespace                      = kubernetes_namespace_v1.lakehouse.metadata[0].name
  chart_package_path             = var.dagster_chart_package_path
  base_values_file               = "${path.root}/../../../helm/values/local/dagster.yaml"
  project_code_image_repository  = var.project_code_image_repository
  project_code_image_tag         = var.project_code_image_tag
  project_code_image_pull_policy = var.project_code_image_pull_policy
  project_code_image_revision    = var.project_code_image_revision
  storage_contract               = local.storage_contract
  catalog_contract               = local.catalog_contract
  governance_contract            = local.governance_contract
  query_contract                 = local.query_contract
  postgresql_contract            = local.metadata_database_contract
  postgresql_ssl_mode            = "require"
  code_locations                 = local.orchestration_contract.code_locations
  floe_manifest_base_uri         = local.artifact_bucket_contract.base_uri
  floe_manifest_access_mode      = local.artifact_bucket_contract.access_mode
  artifact_bucket_name           = local.artifact_bucket_contract.bucket_name
  artifact_base_uri              = local.artifact_bucket_contract.artifact_base_uri
  floe_report_base_uri           = local.artifact_bucket_contract.floe_report_base_uri
  log_base_uri                   = local.artifact_bucket_contract.log_base_uri
  run_artifact_base_uri          = local.artifact_bucket_contract.run_artifact_base_uri
  service_account_annotations    = local.service_account_annotations

  depends_on = [
    module.trino,
    module.openmetadata,
    module.rds_postgresql,
    module.superset,
    aws_eks_pod_identity_association.lakehouse_workloads,
  ]
}
