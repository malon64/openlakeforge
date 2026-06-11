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
  config_context = var.kube_context
}

provider "helm" {
  repository_cache       = local.helm_repository_cache_path
  repository_config_path = local.helm_repository_config_path

  kubernetes = {
    config_path    = local.kubeconfig_path
    config_context = var.kube_context
  }
}

locals {
  kubeconfig_path             = var.kubeconfig_path != null ? pathexpand(var.kubeconfig_path) : pathexpand("~/.kube/config")
  helm_repository_cache_path  = abspath("${path.root}/../../../../.tmp/helm/repository-cache")
  helm_repository_config_path = abspath("${path.root}/../../../../.tmp/helm/repositories.yaml")
  sales_floe_manifest_uri     = "s3://${var.code_bucket_name}/floe/sales/sales.manifest.json"
  polaris_bootstrap_hash      = filesha256("${path.root}/../../modules/catalog/polaris/main.tf")
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
    var.iceberg_bucket_name,
    var.code_bucket_name,
  ]
  region = var.s3_region
}

locals {
  local_provider_name = "local"

  cluster_contract = {
    provider             = local.local_provider_name
    implementation       = "kind"
    namespace            = var.namespace
    kubeconfig_path      = local.kubeconfig_path
    platform_apply_model = "active-kubernetes-context"
  }

  storage_contract = merge(module.seaweedfs.contract, {
    provider       = local.local_provider_name
    implementation = "seaweedfs"
    auth_mode      = "static-access-key-secret"
    ssl_mode       = "disabled"
    ingress_mode   = "cluster-internal"
  })

  metadata_database_contract = merge(module.postgresql.contract, {
    provider       = local.local_provider_name
    implementation = "in-cluster-postgresql"
    auth_mode      = "static-password-secret"
    ssl_mode       = "disabled"
    endpoint       = "${module.postgresql.contract.host}:${module.postgresql.contract.port}"
  })

  catalog_contract = merge(module.polaris.contract, {
    provider                   = local.local_provider_name
    implementation             = "polaris"
    catalog_provider           = "polaris"
    catalog_type               = "rest"
    catalog_name               = var.catalog_name
    runtime_profile            = "polaris-rest"
    trino_catalog_name         = "iceberg"
    default_warehouse_location = "s3://${local.storage_contract.bucket_name}"
    auth_mode                  = "oauth-client-secret"
    ssl_mode                   = "disabled"
    endpoint                   = module.polaris.contract.rest_uri
    ingress_mode               = "cluster-internal"
  })

  governance_contract = merge(module.openmetadata.contract, {
    provider       = local.local_provider_name
    implementation = "openmetadata"
    auth_mode      = "local-development"
    endpoint       = "http://${module.openmetadata.contract.service_name}:${module.openmetadata.contract.http_port}"
    ingress_mode   = "cluster-internal"
  })

  reporting_contract = merge(module.superset.contract, {
    provider       = local.local_provider_name
    implementation = "superset"
    auth_mode      = "local-development"
    endpoint       = "http://${module.superset.contract.service_name}:${module.superset.contract.http_port}"
    ingress_mode   = "cluster-internal"
  })

  artifact_contract = {
    provider                   = local.local_provider_name
    implementation             = "local-build-kind-load-and-s3-upload"
    project_code_image         = "${var.project_code_image_repository}:${var.project_code_image_tag}"
    project_code_image_policy  = var.project_code_image_pull_policy
    superset_image             = "${var.superset_image_repository}:${var.superset_image_tag}"
    superset_image_policy      = var.superset_image_pull_policy
    floe_manifest_uri          = local.sales_floe_manifest_uri
    floe_manifest_distribution = "seaweedfs-code-bucket"
  }

  secrets_contract = {
    provider       = local.local_provider_name
    implementation = "kubernetes-secrets"
    backend        = "kubernetes"
    rotation_mode  = "manual-development"
  }

  identity_contract = {
    provider       = local.local_provider_name
    implementation = "local-development-credentials"
    auth_mode      = "basic-local"
    oidc_enabled   = false
  }

  access_contract = {
    provider       = local.local_provider_name
    implementation = "kubectl-port-forward"
    ingress_mode   = "port-forward"
    tls_mode       = "none-development"
  }

  provider_contracts = {
    cluster           = local.cluster_contract
    storage           = local.storage_contract
    metadata_database = local.metadata_database_contract
    catalog           = local.catalog_contract
    governance        = local.governance_contract
    reporting         = local.reporting_contract
    artifacts         = local.artifact_contract
    secrets           = local.secrets_contract
    identity          = local.identity_contract
    access            = local.access_contract
  }
}

module "polaris" {
  source = "../../modules/catalog/polaris"

  namespace        = kubernetes_namespace_v1.lakehouse.metadata[0].name
  base_values_file = "${path.root}/../../../helm/values/local/polaris.yaml"
  catalog_name     = var.catalog_name
  principal_name   = "trino"
  principal_role   = "data-engineer"
  catalog_role     = "catalog-admin"
  storage_contract = local.storage_contract

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

  namespace           = kubernetes_namespace_v1.lakehouse.metadata[0].name
  base_values_file    = "${path.root}/../../../helm/values/local/openmetadata.yaml"
  deps_values_file    = "${path.root}/../../../helm/values/local/openmetadata-deps.yaml"
  catalog_contract    = local.catalog_contract
  storage_contract    = local.storage_contract
  postgresql_contract = local.metadata_database_contract

  depends_on = [
    module.polaris,
    module.postgresql,
    module.seaweedfs,
  ]
}

module "superset" {
  source = "../../modules/analytics/superset"

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
  floe_manifest_uri              = local.sales_floe_manifest_uri

  depends_on = [
    module.trino,
    module.openmetadata,
    module.postgresql,
    module.superset,
  ]
}
