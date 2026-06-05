output "storage_contract" {
  description = "Non-secret S3 contract consumed by Polaris and Trino."
  value       = module.seaweedfs.contract
}

output "sales_floe_manifest_uri" {
  description = "S3 URI of the Sales Floe manifest consumed by Dagster and Floe."
  value       = local.sales_floe_manifest_uri
}

output "catalog_contract" {
  description = "Non-secret Polaris REST catalog contract consumed by Trino."
  value       = module.polaris.contract
}

output "dagster_webserver_service_name" {
  description = "Dagster webserver service name."
  value       = module.dagster.webserver_service_name
}

output "dagster_code_location_name" {
  description = "Dagster code location name."
  value       = module.dagster.code_location_name
}

output "superset_contract" {
  description = "Non-secret Superset reporting contract."
  value       = module.superset.contract
}
