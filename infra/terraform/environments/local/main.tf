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
  kubeconfig_path = var.kubeconfig_path != null ? abspath(pathexpand(var.kubeconfig_path)) : abspath("${path.root}/../../../../.tmp/kubeconfigs/local.yaml")
  helm_repository_cache_path = (
    var.helm_repository_cache_path != null
    ? abspath(pathexpand(var.helm_repository_cache_path))
    : abspath("${path.root}/../../../../.tmp/helm/local/repository-cache")
  )
  helm_repository_config_path = (
    var.helm_repository_config_path != null
    ? abspath(pathexpand(var.helm_repository_config_path))
    : abspath("${path.root}/../../../../.tmp/helm/local/repositories.yaml")
  )
  artifact_base_uri      = "s3://${var.ops_bucket_name}"
  floe_manifest_base_uri = "${local.artifact_base_uri}/floe/manifests"
  floe_report_base_uri   = "${local.artifact_base_uri}/floe/reports"
  log_base_uri           = "${local.artifact_base_uri}/logs"
  run_artifact_base_uri  = "${local.artifact_base_uri}/run-artifacts"
  domain_floe_manifest_uris = {
    sales        = "${local.floe_manifest_base_uri}/sales/sales.manifest.json"
    supply_chain = "${local.floe_manifest_base_uri}/supply_chain/supply_chain.manifest.json"
  }
  # Catalog namespaces themselves are not declared here. `olf catalog
  # sync-namespaces` reconciles them from the lakehouse inventory during
  # artifacts-deploy (ADR 0002); this root only records which naming model
  # those namespaces follow.
  catalog_namespace_model = "medallion-owner"

  # The resolved topology drives every namespace, service instance, and
  # capability gate below. Nothing here re-reads the Deployment Profile.
  enabled_stages   = { for name, stage in var.stages : name => stage if stage.enabled }
  analytics_stages = { for name, stage in local.enabled_stages : name => stage if stage.analytics }
  # Shared services are provisioned once for the whole deployment, so
  # governance follows any enabled stage asking for it.
  governance_enabled = length([for stage in values(local.enabled_stages) : true if stage.governance]) > 0

  stage_namespaces = { for name in keys(local.enabled_stages) : name => "olf-${name}" }

  # The provider contract still exports the v2 single-stage shape, so one
  # stage has to be the one a runtime consumer resolves. DEV is that stage
  # wherever it is enabled -- every promotion sources from it (ADR 0011).
  # #114 replaces this with the v3 stage index.
  selected_stage           = contains(keys(local.enabled_stages), "dev") ? "dev" : sort(keys(local.enabled_stages))[0]
  selected_stage_namespace = local.stage_namespaces[local.selected_stage]

  # OpenMetadata registers one Superset dashboard service. With no analytics
  # stage there is nothing to register, but the payload still needs a
  # well-formed URL, so it names where that stage's Superset would run.
  governance_superset_url = local.selected_stage_analytics ? local.reporting_contract.endpoint : "http://superset.${local.stage_namespaces[local.selected_stage]}:8088"
  stage_service_accounts  = { for name in keys(local.enabled_stages) : name => "olf-${name}-runtime" }
  stage_labels = { for name in keys(local.enabled_stages) : name => {
    "openlakeforge.io/stage"      = name
    "openlakeforge.io/managed-by" = "openlakeforge"
    "openlakeforge.io/profile"    = var.profile_name
  } }

  # One isolated metadata database per stage-scoped service instance, plus the
  # shared OpenMetadata one. Two Dagster instances sharing a database would
  # corrupt each other's run state; #134 completes the rest of that isolation.
  stage_databases = merge(
    {
      for name in keys(local.enabled_stages) : "dagster_${name}" => {
        key                     = "dagster_${name}"
        db_name                 = "dagster_${name}"
        db_user                 = "dagster_${name}"
        credentials_secret_name = "postgresql-dagster-${name}-creds"
        namespaces              = [local.stage_namespaces[name]]
      }
    },
    {
      for name in keys(local.analytics_stages) : "superset_${name}" => {
        key                     = "superset_${name}"
        db_name                 = "superset_${name}"
        db_user                 = "superset_${name}"
        credentials_secret_name = "postgresql-superset-${name}-creds"
        namespaces              = [local.stage_namespaces[name]]
      }
    },
    local.governance_enabled ? {
      openmetadata = {
        key                     = "openmetadata"
        db_name                 = "openmetadata_db"
        db_user                 = "openmetadata_user"
        credentials_secret_name = "postgresql-openmetadata-creds"
        namespaces              = [var.shared_namespace]
      }
    } : {},
  )
  polaris_bootstrap_hash = sha256(join("", [
    for f in sort(fileset("${path.root}/../../modules/catalog/polaris", "**/*.{tf,tftpl}")) :
    filesha256("${path.root}/../../modules/catalog/polaris/${f}")
  ]))
}

resource "kubernetes_namespace_v1" "shared" {
  metadata {
    name = var.shared_namespace
    labels = {
      "openlakeforge.io/managed-by" = "openlakeforge"
      "openlakeforge.io/profile"    = var.profile_name
      "openlakeforge.io/scope"      = "shared"
    }
  }
}

resource "kubernetes_namespace_v1" "stage" {
  for_each = local.stage_namespaces

  metadata {
    name   = each.value
    labels = local.stage_labels[each.key]
  }
}

# The identity a stage's workloads run as. #114 binds storage and catalog
# permissions to it; here it exists so every stage-scoped pod already has a
# distinct, stage-labelled principal to attach them to.
resource "kubernetes_service_account_v1" "stage_runtime" {
  for_each = local.stage_service_accounts

  metadata {
    name      = each.value
    namespace = kubernetes_namespace_v1.stage[each.key].metadata[0].name
    labels    = local.stage_labels[each.key]
  }
}

module "postgresql" {
  source = "../../modules/storage/postgresql"

  namespace = kubernetes_namespace_v1.shared.metadata[0].name
  databases = values(local.stage_databases)

  depends_on = [
    kubernetes_namespace_v1.stage,
  ]
}

module "seaweedfs" {
  source = "../../modules/storage/seaweedfs"

  namespace          = kubernetes_namespace_v1.shared.metadata[0].name
  base_values_file   = "${path.root}/../../../helm/values/local/seaweedfs.yaml"
  chart_package_path = var.seaweedfs_chart_package_path
  bucket_names = [
    var.bronze_bucket_name,
    var.silver_bucket_name,
    var.gold_bucket_name,
    var.ops_bucket_name,
  ]
  region              = var.s3_region
  workload_namespaces = values(local.stage_namespaces)

  depends_on = [
    kubernetes_namespace_v1.stage,
  ]
}

module "polaris" {
  source = "../../modules/catalog/polaris"

  namespace           = kubernetes_namespace_v1.shared.metadata[0].name
  base_values_file    = "${path.root}/../../../helm/values/local/polaris.yaml"
  chart_package_path  = var.polaris_chart_package_path
  catalog_name        = var.catalog_name
  principal_name      = "trino"
  principal_role      = "data-engineer"
  catalog_role        = "catalog-admin"
  storage_contract    = local.storage_contract
  postgresql_contract = local.metadata_database_contract
  bootstrap_revision  = local.polaris_bootstrap_hash
  workload_namespaces = values(local.stage_namespaces)

  depends_on = [
    module.seaweedfs,
    kubernetes_namespace_v1.stage,
  ]
}

module "trino" {
  source = "../../modules/query/trino"

  namespace                  = kubernetes_namespace_v1.shared.metadata[0].name
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
  count  = local.governance_enabled ? 1 : 0

  namespace               = kubernetes_namespace_v1.shared.metadata[0].name
  base_values_file        = "${path.root}/../../../helm/values/local/openmetadata.yaml"
  deps_values_file        = "${path.root}/../../../helm/values/local/openmetadata-deps.yaml"
  chart_package_path      = var.openmetadata_chart_package_path
  deps_chart_package_path = var.openmetadata_deps_chart_package_path
  catalog_contract        = local.catalog_contract
  storage_contract        = local.storage_contract
  postgresql_contract     = local.metadata_database_contract
  # Empty by design: the database schemas mirror Polaris namespaces, which now
  # come into existence in Phase 2. `olf openmetadata deploy-metadata` creates
  # each databaseSchema entity right before it seeds that schema's tables.
  catalog_schema_names = []
  workload_namespaces  = values(local.stage_namespaces)
  # Superset is stage-scoped, so the shared governance service has to be told
  # which stage's instance it registers as a dashboard service.
  superset_url            = local.governance_superset_url
  trino_lineage_namespace = "trino://${local.query_contract.service_name}.${var.shared_namespace}:${local.query_contract.http_port}"

  depends_on = [
    module.polaris,
    module.postgresql,
    module.seaweedfs,
    kubernetes_namespace_v1.stage,
  ]
}

module "superset" {
  source   = "../../modules/analytics/superset"
  for_each = local.analytics_stages

  namespace           = kubernetes_namespace_v1.stage[each.key].metadata[0].name
  base_values_file    = "${path.root}/../../../helm/values/local/superset.yaml"
  chart_package_path  = var.superset_chart_package_path
  image_repository    = var.superset_image_repository
  image_tag           = var.superset_image_tag
  image_pull_policy   = var.superset_image_pull_policy
  postgresql_contract = local.stage_metadata_database_contracts[each.key]

  depends_on = [
    module.postgresql,
    module.trino,
  ]
}

moved {
  from = module.openmetadata
  to   = module.openmetadata[0]
}

module "dagster" {
  source   = "../../modules/orchestration/dagster"
  for_each = local.enabled_stages

  namespace                      = kubernetes_namespace_v1.stage[each.key].metadata[0].name
  base_values_file               = "${path.root}/../../../helm/values/local/dagster.yaml"
  chart_package_path             = var.dagster_chart_package_path
  project_code_image_repository  = var.project_code_image_repository
  project_code_image_tag         = var.project_code_image_tag
  project_code_image_pull_policy = var.project_code_image_pull_policy
  project_code_image_revision    = var.project_code_image_revision
  storage_contract               = local.storage_contract
  catalog_contract               = local.catalog_contract
  # The shared governance service exists whenever any stage enables it, but a
  # stage that did not ask for governance must not receive OpenLineage
  # configuration or the ingestion-bot credential: capabilities are per stage
  # (ADR 0011).
  governance_contract = merge(local.governance_contract, {
    enabled = local.governance_enabled && each.value.governance
  })
  query_contract            = local.query_contract
  postgresql_contract       = local.stage_metadata_database_contracts[each.key]
  code_locations            = local.orchestration_contract.code_locations
  floe_manifest_base_uri    = local.artifact_bucket_contract.base_uri
  floe_manifest_access_mode = local.artifact_bucket_contract.access_mode
  artifact_bucket_name      = local.artifact_bucket_contract.bucket_name
  artifact_base_uri         = local.artifact_bucket_contract.artifact_base_uri
  floe_report_base_uri      = local.artifact_bucket_contract.floe_report_base_uri
  log_base_uri              = local.artifact_bucket_contract.log_base_uri
  run_artifact_base_uri     = local.artifact_bucket_contract.run_artifact_base_uri

  depends_on = [
    module.trino,
    module.openmetadata,
    module.postgresql,
    module.superset,
  ]
}
