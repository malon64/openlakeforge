locals {
  rest_uri              = "https://glue.${var.region}.amazonaws.com/iceberg"
  catalog_namespace_map = { for namespace in var.catalog_namespaces : namespace.name => namespace }
  catalog_schema_names  = keys(local.catalog_namespace_map)
}

resource "aws_glue_catalog_database" "namespace" {
  for_each = local.catalog_namespace_map

  name         = each.value.name
  catalog_id   = var.account_id
  location_uri = each.value.location

  description = "OpenLakeForge ${var.catalog_name} ${each.value.name} Iceberg namespace"
}
