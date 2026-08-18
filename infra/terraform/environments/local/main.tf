terraform {
  required_version = ">= 1.7.0"

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
  kubeconfig_path             = var.kubeconfig_path != null ? abspath(pathexpand(var.kubeconfig_path)) : abspath("${path.root}/../../../../.tmp/kubeconfigs/local.yaml")
  helm_repository_cache_path  = abspath("${path.root}/../../../../.tmp/helm/local/repository-cache")
  helm_repository_config_path = abspath("${path.root}/../../../../.tmp/helm/local/repositories.yaml")
  artifact_base_uri           = "s3://${var.ops_bucket_name}"
  floe_manifest_base_uri      = "${local.artifact_base_uri}/floe/manifests"
  floe_report_base_uri        = "${local.artifact_base_uri}/floe/reports"
  log_base_uri                = "${local.artifact_base_uri}/logs"
  run_artifact_base_uri       = "${local.artifact_base_uri}/run-artifacts"
  product_floe_manifest_uris = {
    sales_order_revenue                = "${local.floe_manifest_base_uri}/sales/order_revenue/order_revenue.manifest.json"
    sales_customer_health              = "${local.floe_manifest_base_uri}/sales/customer_health/customer_health.manifest.json"
    supply_chain_inventory_reliability = "${local.floe_manifest_base_uri}/supply_chain/inventory_reliability/inventory_reliability.manifest.json"
  }
  # Namespaces themselves are not declared here. `olf catalog sync-namespaces`
  # reconciles them from domains/*/domain.yaml during artifacts-deploy (ADR
  # 0022); this root only records which naming model those namespaces follow.
  catalog_namespace_model = "product-layer"
  polaris_bootstrap_hash = sha256(join("", [
    for f in sort(fileset("${path.root}/../../modules/catalog/polaris", "**/*.{tf,tftpl}")) :
    filesha256("${path.root}/../../modules/catalog/polaris/${f}")
  ]))
}

resource "kubernetes_namespace_v1" "lakehouse" {
  metadata {
    name = var.namespace
  }
}

module "postgresql" {
  source = "../../modules/storage/postgresql"

  namespace           = kubernetes_namespace_v1.lakehouse.metadata[0].name
  enable_openmetadata = var.enable_governance
  enable_superset     = var.enable_analytics
}

module "seaweedfs" {
  source = "../../modules/storage/seaweedfs"

  namespace        = kubernetes_namespace_v1.lakehouse.metadata[0].name
  base_values_file = "${path.root}/../../../helm/values/local/seaweedfs.yaml"
  bucket_names = [
    var.bronze_bucket_name,
    var.silver_bucket_name,
    var.gold_bucket_name,
    var.ops_bucket_name,
  ]
  region = var.s3_region
}

module "polaris" {
  source = "../../modules/catalog/polaris"

  namespace           = kubernetes_namespace_v1.lakehouse.metadata[0].name
  base_values_file    = "${path.root}/../../../helm/values/local/polaris.yaml"
  catalog_name        = var.catalog_name
  principal_name      = "trino"
  principal_role      = "data-engineer"
  catalog_role        = "catalog-admin"
  storage_contract    = local.storage_contract
  postgresql_contract = local.metadata_database_contract
  bootstrap_revision  = local.polaris_bootstrap_hash

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
  count  = var.enable_governance ? 1 : 0

  namespace           = kubernetes_namespace_v1.lakehouse.metadata[0].name
  base_values_file    = "${path.root}/../../../helm/values/local/openmetadata.yaml"
  deps_values_file    = "${path.root}/../../../helm/values/local/openmetadata-deps.yaml"
  catalog_contract    = local.catalog_contract
  storage_contract    = local.storage_contract
  postgresql_contract = local.metadata_database_contract
  # Empty by design: the database schemas mirror Polaris namespaces, which now
  # come into existence in Phase 2. `olf openmetadata deploy-metadata` creates
  # each databaseSchema entity right before it seeds that schema's tables.
  catalog_schema_names = []

  depends_on = [
    module.polaris,
    module.postgresql,
    module.seaweedfs,
  ]
}

module "superset" {
  source = "../../modules/analytics/superset"
  count  = var.enable_analytics ? 1 : 0

  namespace           = kubernetes_namespace_v1.lakehouse.metadata[0].name
  base_values_file    = "${path.root}/../../../helm/values/local/superset.yaml"
  image_repository    = var.superset_image_repository
  image_tag           = var.superset_image_tag
  image_pull_policy   = var.superset_image_pull_policy
  postgresql_contract = local.metadata_database_contract
  depends_on = [
    module.postgresql,
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
  base_values_file               = "${path.root}/../../../helm/values/local/dagster.yaml"
  project_code_image_repository  = var.project_code_image_repository
  project_code_image_tag         = var.project_code_image_tag
  project_code_image_pull_policy = var.project_code_image_pull_policy
  project_code_image_revision    = var.project_code_image_revision
  storage_contract               = local.storage_contract
  catalog_contract               = local.catalog_contract
  governance_contract            = local.governance_contract
  postgresql_contract            = local.metadata_database_contract
  code_locations                 = local.orchestration_contract.code_locations
  floe_manifest_base_uri         = local.artifact_bucket_contract.base_uri
  floe_manifest_access_mode      = local.artifact_bucket_contract.access_mode
  artifact_bucket_name           = local.artifact_bucket_contract.bucket_name
  artifact_base_uri              = local.artifact_bucket_contract.artifact_base_uri
  floe_report_base_uri           = local.artifact_bucket_contract.floe_report_base_uri
  log_base_uri                   = local.artifact_bucket_contract.log_base_uri
  run_artifact_base_uri          = local.artifact_bucket_contract.run_artifact_base_uri

  depends_on = [
    module.trino,
    module.openmetadata,
    module.postgresql,
    module.superset,
  ]
}
