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
  kubernetes = {
    config_path    = local.kubeconfig_path
    config_context = var.kube_context
  }
}

locals {
  kubeconfig_path = var.kubeconfig_path != null ? pathexpand(var.kubeconfig_path) : pathexpand("~/.kube/config")
}

resource "kubernetes_namespace_v1" "lakehouse" {
  metadata {
    name = var.namespace
  }
}

module "seaweedfs" {
  source = "../../modules/storage/seaweedfs"

  namespace        = kubernetes_namespace_v1.lakehouse.metadata[0].name
  base_values_file = "${path.root}/../../../helm/values/local/seaweedfs.yaml"
  bucket_names = [
    var.iceberg_bucket_name,
  ]
  region = var.s3_region
}

module "polaris" {
  source = "../../modules/catalog/polaris"

  namespace        = kubernetes_namespace_v1.lakehouse.metadata[0].name
  base_values_file = "${path.root}/../../../helm/values/local/polaris.yaml"
  catalog_name     = var.catalog_name
  principal_name   = "trino"
  principal_role   = "data-engineer"
  catalog_role     = "catalog-admin"
  storage_contract = module.seaweedfs.contract

  depends_on = [
    module.seaweedfs,
  ]
}

module "trino" {
  source = "../../modules/query/trino"

  namespace        = kubernetes_namespace_v1.lakehouse.metadata[0].name
  base_values_file = "${path.root}/../../../helm/values/local/trino.yaml"
  storage_contract = module.seaweedfs.contract
  catalog_contract = module.polaris.contract

  depends_on = [
    module.polaris,
  ]
}
