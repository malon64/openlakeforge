output "contract" {
  description = "Polaris REST catalog contract for Trino."
  value = {
    rest_uri                      = local.rest_uri
    token_uri                     = local.token_uri
    warehouse                     = var.catalog_name
    oauth_scope                   = local.oauth_scope
    trino_credentials_secret_name = var.trino_credentials_secret_name
    trino_client_id_key           = "POLARIS_TRINO_CLIENT_ID"
    trino_client_secret_key       = "POLARIS_TRINO_CLIENT_SECRET"
  }

  depends_on = [
    kubernetes_job_v1.bootstrap,
  ]
}

output "root_client_secret" {
  description = "Generated local Polaris root client secret. Exposed only for debugging."
  value       = random_password.root_client_secret.result
  sensitive   = true
}
