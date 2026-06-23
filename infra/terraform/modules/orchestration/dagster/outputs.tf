output "webserver_service_name" {
  description = "Dagster webserver service name."
  value       = "${var.release_name}-dagster-webserver"
}

output "webserver_port" {
  description = "Dagster webserver service port."
  value       = 80
}

output "code_location_name" {
  description = "First Dagster code location name. Kept for compatibility with older scripts."
  value       = var.code_locations[0].name
}

output "code_location_names" {
  description = "Dagster code location names."
  value       = [for location in var.code_locations : location.name]
}
