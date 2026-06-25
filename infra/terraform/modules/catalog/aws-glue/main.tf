resource "aws_glue_catalog_database" "namespace" {
  for_each = {
    for namespace in var.catalog_namespaces : namespace.name => namespace
  }

  name         = each.value.name
  catalog_id   = var.account_id
  location_uri = each.value.location

  description = "OpenLakeForge ${var.catalog_name} ${each.value.name} Iceberg namespace"
}

locals {
  rest_uri = "https://glue.${var.region}.amazonaws.com/iceberg"
}
