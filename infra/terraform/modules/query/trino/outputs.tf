output "service_name" {
  description = "Trino coordinator service name."
  value       = var.release_name
}

output "http_port" {
  description = "Trino HTTP service port."
  value       = 8080
}
