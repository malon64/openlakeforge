output "storage_contract" {
  description = "Non-secret S3-compatible storage contract consumed by platform modules."
  value       = local.storage_contract
}

output "foundation_contract" {
  description = "Provider-neutral foundation contract consumed by the local platform."
  value       = local.foundation_contract
}

output "kubernetes_platform_contract" {
  description = "Provider-neutral Kubernetes platform contract consumed by local modules and scripts."
  value       = local.kubernetes_platform_contract
}

output "floe_manifest_base_uri" {
  description = "S3 base URI of product Floe manifests consumed by Dagster and Floe."
  value       = local.floe_manifest_base_uri
}

output "product_floe_manifest_uris" {
  description = "S3 URIs of product Floe manifests consumed by Dagster and Floe."
  value       = local.product_floe_manifest_uris
}

output "catalog_contract" {
  description = "Non-secret Iceberg catalog contract consumed by platform modules."
  value       = local.catalog_contract
}

output "metadata_database_contract" {
  description = "Non-secret metadata database contract consumed by platform modules."
  value       = local.metadata_database_contract
}

output "dagster_webserver_service_name" {
  description = "Dagster webserver service name."
  value       = module.dagster.webserver_service_name
}

output "dagster_code_location_name" {
  description = "First Dagster code location name. Kept for compatibility with older scripts."
  value       = module.dagster.code_location_name
}

output "dagster_code_location_names" {
  description = "Dagster code location names."
  value       = module.dagster.code_location_names
}

output "superset_contract" {
  description = "Non-secret Superset reporting contract."
  value       = local.reporting_contract
}

output "artifact_registry_contract" {
  description = "Runtime image registry/distribution contract."
  value       = local.artifact_registry_contract
}

output "artifact_bucket_contract" {
  description = "Object-storage artifact bucket contract."
  value       = local.artifact_bucket_contract
}

output "secrets_contract" {
  description = "Secret reference and delivery contract."
  value       = local.secrets_contract
}

output "identity_contract" {
  description = "Identity and workload identity contract."
  value       = local.identity_contract
}

output "access_contract" {
  description = "Internal and external service access contract."
  value       = local.access_contract
}

output "provider_contracts" {
  description = "Provider-neutral contract map for the local implementation."
  value       = local.provider_contracts
}
