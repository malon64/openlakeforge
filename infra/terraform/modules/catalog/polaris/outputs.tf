output "contract" {
  description = "Polaris REST catalog contract for platform consumers."
  value = {
    catalog_type                  = "rest"
    catalog_provider              = "polaris"
    catalog_name                  = var.catalog_name
    runtime_profile               = "polaris-rest"
    trino_catalog_name            = "iceberg"
    default_warehouse_location    = "s3://${local.silver_bucket_name}"
    catalog_namespaces            = local.catalog_namespaces
    catalog_namespace_names       = [for namespace in local.catalog_namespaces : namespace.name]
    rest_uri                      = local.rest_uri
    token_uri                     = local.token_uri
    warehouse                     = var.catalog_name
    oauth_scope                   = local.oauth_scope
    trino_credentials_secret_name = var.trino_credentials_secret_name
    trino_client_id_key           = "POLARIS_TRINO_CLIENT_ID"
    trino_client_secret_key       = "POLARIS_TRINO_CLIENT_SECRET"
    floe_credentials_secret_name  = var.floe_credentials_secret_name
    floe_client_id_key            = "POLARIS_FLOE_CLIENT_ID"
    floe_client_secret_key        = "POLARIS_FLOE_CLIENT_SECRET"
    dbt_credentials_secret_name   = var.dbt_credentials_secret_name
    dbt_client_id_key             = "POLARIS_DBT_CLIENT_ID"
    dbt_client_secret_key         = "POLARIS_DBT_CLIENT_SECRET"
    bootstrap_run_id              = kubernetes_job_v1.bootstrap.metadata[0].name
    om_credentials_secret_name    = var.om_credentials_secret_name
    om_client_id_key              = "POLARIS_OM_CLIENT_ID"
    om_client_secret_key          = "POLARIS_OM_CLIENT_SECRET"
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
