terraform {
  required_version = ">= 1.6.0"

  required_providers {
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
  kubeconfig_path             = var.kubeconfig_path != null ? pathexpand(var.kubeconfig_path) : coalesce(try(local.foundation_contract.kubeconfig_path, null), pathexpand("~/.kube/config"))
  helm_repository_cache_path  = abspath("${path.root}/../../../../.tmp/helm/repository-cache")
  helm_repository_config_path = abspath("${path.root}/../../../../.tmp/helm/repositories.yaml")
  floe_manifest_base_uri      = "s3://${var.code_bucket_name}/floe"
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
        location = "s3://${var.silver_bucket_name}/${namespaces.silver}/"
      },
      {
        name     = namespaces.gold
        location = "s3://${var.gold_bucket_name}/${namespaces.gold}/"
      },
    ]
  ])
  polaris_bootstrap_hash = filesha256("${path.root}/../../modules/catalog/polaris/main.tf")
}

resource "kubernetes_namespace_v1" "lakehouse" {
  metadata {
    name = var.namespace
  }
}

module "postgresql" {
  source = "../../modules/storage/postgresql"

  namespace = kubernetes_namespace_v1.lakehouse.metadata[0].name
}

module "seaweedfs" {
  source = "../../modules/storage/seaweedfs"

  namespace        = kubernetes_namespace_v1.lakehouse.metadata[0].name
  base_values_file = "${path.root}/../../../helm/values/local/seaweedfs.yaml"
  bucket_names = [
    var.bronze_bucket_name,
    var.silver_bucket_name,
    var.gold_bucket_name,
    var.code_bucket_name,
  ]
  region = var.s3_region
}

module "polaris" {
  source = "../../modules/catalog/polaris"

  namespace            = kubernetes_namespace_v1.lakehouse.metadata[0].name
  base_values_file     = "${path.root}/../../../helm/values/local/polaris.yaml"
  catalog_name         = var.catalog_name
  principal_name       = "trino"
  principal_role       = "data-engineer"
  catalog_role         = "catalog-admin"
  storage_contract     = local.storage_contract
  catalog_namespaces   = local.catalog_namespaces
  bootstrap_generation = var.polaris_bootstrap_generation
  bootstrap_revision   = local.polaris_bootstrap_hash

  depends_on = [
    module.seaweedfs,
  ]
}

module "trino" {
  source = "../../modules/query/trino"

  namespace                  = kubernetes_namespace_v1.lakehouse.metadata[0].name
  base_values_file           = "${path.root}/../../../helm/values/local/trino.yaml"
  chart_package_path         = var.trino_chart_package_path
  storage_contract           = local.storage_contract
  catalog_contract           = local.catalog_contract
  catalog_bootstrap_revision = local.polaris_bootstrap_hash

  depends_on = [
    module.polaris,
  ]
}

module "openmetadata" {
  source = "../../modules/governance/openmetadata"

  namespace            = kubernetes_namespace_v1.lakehouse.metadata[0].name
  base_values_file     = "${path.root}/../../../helm/values/local/openmetadata.yaml"
  deps_values_file     = "${path.root}/../../../helm/values/local/openmetadata-deps.yaml"
  catalog_contract     = local.catalog_contract
  storage_contract     = local.storage_contract
  postgresql_contract  = local.metadata_database_contract
  catalog_schema_names = [for namespace in local.catalog_namespaces : namespace.name]

  depends_on = [
    module.polaris,
    module.postgresql,
    module.seaweedfs,
  ]
}

module "superset" {
  source = "../../modules/analytics/superset"

  namespace                  = kubernetes_namespace_v1.lakehouse.metadata[0].name
  base_values_file           = "${path.root}/../../../helm/values/local/superset.yaml"
  image_repository           = var.superset_image_repository
  image_tag                  = var.superset_image_tag
  image_pull_policy          = var.superset_image_pull_policy
  reports_storage_class_name = "azurefile-csi"
  reports_access_modes       = ["ReadWriteMany"]
  postgresql_contract        = local.metadata_database_contract

  depends_on = [
    module.postgresql,
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
  floe_manifest_base_uri         = local.artifact_bucket_contract.base_uri
  floe_manifest_access_mode      = local.artifact_bucket_contract.access_mode

  depends_on = [
    module.trino,
    module.openmetadata,
    module.postgresql,
    module.superset,
  ]
}
