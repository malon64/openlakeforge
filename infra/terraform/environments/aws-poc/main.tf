terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
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
  aws_region                  = coalesce(try(local.foundation_contract.aws_region, null), var.aws_region)
  kubeconfig_path             = var.kubeconfig_path != null ? pathexpand(var.kubeconfig_path) : coalesce(try(local.foundation_contract.kubeconfig_path, null), pathexpand("~/.kube/config"))
  helm_repository_cache_path  = abspath("${path.root}/../../../../.tmp/helm/repository-cache")
  helm_repository_config_path = abspath("${path.root}/../../../../.tmp/helm/repositories.yaml")
  artifact_base_uri           = "s3://${local.storage_contract.ops_bucket_name}"
  floe_manifest_base_uri      = "${local.artifact_base_uri}/floe/manifests"
  floe_report_base_uri        = "${local.artifact_base_uri}/floe/reports"
  log_base_uri                = "${local.artifact_base_uri}/logs"
  run_artifact_base_uri       = "${local.artifact_base_uri}/run-artifacts"
  product_floe_manifest_uris = {
    sales_order_revenue                = "${local.floe_manifest_base_uri}/sales/order_revenue/order_revenue.manifest.json"
    sales_customer_health              = "${local.floe_manifest_base_uri}/sales/customer_health/customer_health.manifest.json"
    supply_chain_inventory_reliability = "${local.floe_manifest_base_uri}/supply_chain/inventory_reliability/inventory_reliability.manifest.json"
  }
  catalog_namespace_model = "product-layer"
  catalog_product_namespaces = {
    sales_order_revenue = {
      silver = "sales_order_revenue_silver"
      gold   = "sales_order_revenue_gold"
    }
    sales_customer_health = {
      silver = "sales_customer_health_silver"
      gold   = "sales_customer_health_gold"
    }
    supply_chain_inventory_reliability = {
      silver = "supply_chain_inventory_reliability_silver"
      gold   = "supply_chain_inventory_reliability_gold"
    }
  }
  catalog_silver_namespaces = {
    for product, namespaces in local.catalog_product_namespaces : product => namespaces.silver
  }
  catalog_gold_namespaces = {
    for product, namespaces in local.catalog_product_namespaces : product => namespaces.gold
  }
  catalog_namespaces = flatten([
    for product, namespaces in local.catalog_product_namespaces : [
      {
        name     = namespaces.silver
        location = "s3://${module.s3.bucket_names.silver}/${namespaces.silver}/"
      },
      {
        name     = namespaces.gold
        location = "s3://${module.s3.bucket_names.gold}/${namespaces.gold}/"
      },
    ]
  ])
  irsa_subjects = [
    "system:serviceaccount:${var.namespace}:dagster",
    "system:serviceaccount:${var.namespace}:dagster-dagster-user-deployments-user-deployments",
    "system:serviceaccount:${var.namespace}:trino",
    "system:serviceaccount:${var.namespace}:openmetadata",
    "system:serviceaccount:${var.namespace}:openmetadata-bootstrap",
  ]
  service_account_annotations = {
    "eks.amazonaws.com/role-arn" = aws_iam_role.lakehouse_workloads.arn
  }
}

resource "kubernetes_namespace_v1" "lakehouse" {
  metadata {
    name = var.namespace
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

  region             = local.aws_region
  account_id         = local.foundation_contract.aws_account_id
  catalog_name       = var.catalog_name
  catalog_namespaces = local.catalog_namespaces
}

module "rds_postgresql" {
  source = "../../modules/storage/rds-postgresql"

  namespace           = kubernetes_namespace_v1.lakehouse.metadata[0].name
  name_prefix         = "openlakeforge"
  vpc_id              = local.foundation_contract.vpc_id
  subnet_ids          = local.foundation_contract.subnet_ids
  allowed_cidr_blocks = [local.foundation_contract.vpc_cidr_block]
  instance_class      = var.rds_instance_class

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
          "s3:ListBucketMultipartUploads",
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

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Federated = local.foundation_contract.oidc_provider_arn
        }
        Action = "sts:AssumeRoleWithWebIdentity"
        Condition = {
          StringEquals = {
            "${local.foundation_contract.oidc_provider_url_without_scheme}:aud" = "sts.amazonaws.com"
          }
          StringLike = {
            "${local.foundation_contract.oidc_provider_url_without_scheme}:sub" = local.irsa_subjects
          }
        }
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lakehouse_workloads" {
  role       = aws_iam_role.lakehouse_workloads.name
  policy_arn = aws_iam_policy.lakehouse_workloads.arn
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

  depends_on = [
    module.glue,
    aws_iam_role_policy_attachment.lakehouse_workloads,
  ]
}

module "openmetadata" {
  source = "../../modules/governance/openmetadata"

  namespace                   = kubernetes_namespace_v1.lakehouse.metadata[0].name
  base_values_file            = "${path.root}/../../../helm/values/local/openmetadata.yaml"
  deps_values_file            = "${path.root}/../../../helm/values/local/openmetadata-deps.yaml"
  catalog_contract            = local.catalog_contract
  storage_contract            = local.storage_contract
  postgresql_contract         = local.metadata_database_contract
  postgresql_ssl_mode         = "require"
  catalog_schema_names        = [for namespace in local.catalog_namespaces : namespace.name]
  catalog_database_name       = var.catalog_name
  catalog_refresh_enabled     = false
  service_account_annotations = local.service_account_annotations

  depends_on = [
    module.glue,
    module.rds_postgresql,
  ]
}

module "superset" {
  source = "../../modules/analytics/superset"

  namespace                  = kubernetes_namespace_v1.lakehouse.metadata[0].name
  base_values_file           = "${path.root}/../../../helm/values/local/superset.yaml"
  image_repository           = var.superset_image_repository
  image_tag                  = var.superset_image_tag
  image_pull_policy          = var.superset_image_pull_policy
  postgresql_contract        = local.metadata_database_contract
  postgresql_ssl_mode        = "require"
  reports_storage_size       = "5Gi"
  reports_storage_class_name = null

  depends_on = [
    module.rds_postgresql,
    module.trino,
  ]
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
  postgresql_contract            = local.metadata_database_contract
  postgresql_ssl_mode            = "require"
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
  ]
}
