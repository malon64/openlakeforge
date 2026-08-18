locals {
  labels = {
    "app.kubernetes.io/name"       = "openmetadata"
    "app.kubernetes.io/managed-by" = "terraform"
    "openlakeforge.io/component"   = "governance"
  }

  om_url                        = "http://${var.release_name}:${var.om_http_port}"
  catalog_schema_names_json     = jsonencode(var.catalog_schema_names)
  catalog_schema_names_json_b64 = base64encode(local.catalog_schema_names_json)
  catalog_type                  = coalesce(try(var.catalog_contract.catalog_type, null), "rest")
  catalog_service_name          = local.catalog_type == "glue" ? "aws_glue" : "polaris"
  catalog_service_display_name  = local.catalog_type == "glue" ? "AWS Glue Data Catalog" : "Polaris Iceberg Catalog"
  catalog_database_fqn          = "${local.catalog_service_name}.${var.catalog_database_name}"
  postgresql_ssl_mode           = var.postgresql_ssl_mode != "" ? var.postgresql_ssl_mode : coalesce(try(var.postgresql_contract.ssl_mode, null), "disable")
  storage_secret_env = var.storage_contract.credentials_secret_name == null ? [] : [
    {
      name        = "AWS_ACCESS_KEY_ID"
      secret_name = var.storage_contract.credentials_secret_name
      key         = coalesce(try(var.storage_contract.access_key_id_key, null), "AWS_ACCESS_KEY_ID")
    },
    {
      name        = "AWS_SECRET_ACCESS_KEY"
      secret_name = var.storage_contract.credentials_secret_name
      key         = coalesce(try(var.storage_contract.secret_access_key_key, null), "AWS_SECRET_ACCESS_KEY")
    },
  ]
  polaris_secret_env = try(var.catalog_contract.om_credentials_secret_name, null) == null ? [] : [
    {
      name        = "POLARIS_OM_CLIENT_ID"
      secret_name = var.catalog_contract.om_credentials_secret_name
      key         = coalesce(try(var.catalog_contract.om_client_id_key, null), "POLARIS_OM_CLIENT_ID")
    },
    {
      name        = "POLARIS_OM_CLIENT_SECRET"
      secret_name = var.catalog_contract.om_credentials_secret_name
      key         = coalesce(try(var.catalog_contract.om_client_secret_key, null), "POLARIS_OM_CLIENT_SECRET")
    },
  ]
  bootstrap_secret_env = concat(local.storage_secret_env, local.polaris_secret_env)
  bootstrap_annotations = {
    "openlakeforge.io/openmetadata-release-revision" = tostring(helm_release.openmetadata.metadata.revision)
    "openlakeforge.io/catalog-schema-hash"           = sha256(local.catalog_schema_names_json)
  }
}
