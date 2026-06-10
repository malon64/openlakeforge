output "contract" {
  description = "Superset reporting contract consumed by local scripts and future modules."
  value = {
    service_name       = var.release_name
    http_port          = var.http_port
    reports_claim_name = local.reports_claim_name
    reports_mount_path = var.reports_mount_path
  }

  depends_on = [
    helm_release.superset,
  ]
}

output "service_name" {
  description = "Superset web service name."
  value       = var.release_name
}

output "http_port" {
  description = "Superset HTTP service port."
  value       = var.http_port
}
