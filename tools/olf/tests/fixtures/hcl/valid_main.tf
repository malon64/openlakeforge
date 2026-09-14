locals {
  catalog_namespace_model = "medallion-owner"

  enabled_stages         = { for name, stage in var.stages : name => stage if stage.enabled }
  analytics_stages       = { for name, stage in local.enabled_stages : name => stage if stage.analytics }
  governance_enabled     = length([for stage in values(local.enabled_stages) : true if stage.governance]) > 0
  stage_namespaces       = { for name in keys(local.enabled_stages) : name => "olf-${name}" }
  stage_service_accounts = { for name in keys(local.enabled_stages) : name => "olf-${name}-runtime" }
  stage_databases        = {}
  selected_stage         = "dev"
}

module "dagster" {
  source   = "../../modules/orchestration/dagster"
  for_each = local.enabled_stages

  namespace           = kubernetes_namespace_v1.stage[each.key].metadata[0].name
  storage_contract    = local.stage_storage_contracts[each.key]
  catalog_contract    = local.stage_catalog_contracts[each.key]
  postgresql_contract = local.stage_metadata_database_contracts[each.key]
  query_contract = merge(local.query_contract, {
    catalog_name = local.stage_catalog_contracts[each.key].catalog_name
  })
}
