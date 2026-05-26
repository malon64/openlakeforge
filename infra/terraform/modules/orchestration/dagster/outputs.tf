output "webserver_service_name" {
  description = "Dagster webserver service name."
  value       = "${var.release_name}-dagster-webserver"
}

output "webserver_port" {
  description = "Dagster webserver service port."
  value       = 80
}

output "code_location_name" {
  description = "Dagster code location name."
  value       = var.code_location_name
}
