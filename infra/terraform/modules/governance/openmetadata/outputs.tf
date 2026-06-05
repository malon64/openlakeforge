output "contract" {
  description = "OpenMetadata governance contract consumed by the Dagster module."
  value = {
    service_name              = var.release_name
    http_port                 = var.om_http_port
    openlineage_url           = "http://${local.openlineage_proxy_name}:${local.openlineage_proxy_port}"
    lineage_endpoint          = "api/v1/lineage"
    ingestion_bot_secret_name = var.ingestion_bot_secret_name
    ingestion_bot_jwt_key     = var.ingestion_bot_jwt_key
  }

  depends_on = [
    kubernetes_job_v1.bootstrap,
    kubernetes_deployment_v1.openlineage_proxy,
    kubernetes_service_v1.openlineage_proxy,
  ]
}
