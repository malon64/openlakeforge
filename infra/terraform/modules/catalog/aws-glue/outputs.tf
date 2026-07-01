output "contract" {
  description = "Iceberg catalog contract implemented by AWS Glue Data Catalog."
  value = {
    rest_uri                     = local.rest_uri
    token_uri                    = null
    warehouse                    = var.account_id
    oauth_scope                  = null
    catalog_type                 = "glue"
    catalog_provider             = "aws-glue"
    catalog_name                 = var.catalog_name
    runtime_profile              = "aws-glue-rest"
    trino_catalog_name           = var.trino_catalog_name
    glue_catalog_id              = var.account_id
    glue_region                  = var.region
    glue_rest_uri                = local.rest_uri
    glue_rest_warehouse          = var.account_id
    glue_database                = null
    glue_database_location       = null
    glue_warehouse_prefix        = "warehouse/iceberg"
    glue_database_names          = local.catalog_schema_names
    glue_schema_names            = local.catalog_schema_names
    catalog_schema_names         = local.catalog_schema_names
    catalog_namespaces           = var.catalog_namespaces
    endpoint                     = local.rest_uri
    auth_mode                    = "aws-sigv4-pod-identity"
    ssl_mode                     = "required"
    ingress_mode                 = "aws-public-service-endpoint"
    om_credentials_secret_name   = null
    om_client_id_key             = null
    om_client_secret_key         = null
    floe_credentials_secret_name = null
    floe_client_id_key           = null
    floe_client_secret_key       = null
    dbt_credentials_secret_name  = null
    dbt_client_id_key            = null
    dbt_client_secret_key        = null
  }

  depends_on = [
    aws_glue_catalog_database.namespace,
  ]
}
