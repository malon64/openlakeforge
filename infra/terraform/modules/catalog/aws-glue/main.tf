locals {
  rest_uri = "https://glue.${var.region}.amazonaws.com/iceberg"
}

# Database lifecycle moved to Phase 2 (ADR 0002): `olf catalog sync-namespaces`
# reconciles Glue databases from the lakehouse descriptors
# (lakehouse_code/lakehouse.yaml + bronze source.yaml) during artifacts-deploy,
# the same way it already reconciles Polaris namespaces. This root no longer
# creates or destroys databases -- it only stands up the catalog service.
#
# `removed` forgets the resource from state without deleting the underlying
# Glue databases, so an existing deployment's tables survive the upgrade. This
# block can be dropped once every environment has applied past this change.
removed {
  from = aws_glue_catalog_database.namespace

  lifecycle {
    destroy = false
  }
}
