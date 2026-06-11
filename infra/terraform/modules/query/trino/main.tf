locals {
  chart      = var.chart_package_path != null ? var.chart_package_path : "trino"
  repository = var.chart_package_path != null ? null : var.chart_repository
  version    = var.chart_package_path != null ? null : var.chart_version
}

resource "helm_release" "trino" {
  name       = var.release_name
  repository = local.repository
  chart      = local.chart
  version    = local.version
  namespace  = var.namespace

  wait    = true
  timeout = 300

  values = [
    file(var.base_values_file),
    yamlencode({
      envFrom = [
        {
          secretRef = {
            name = var.storage_contract.credentials_secret_name
          }
        },
        {
          secretRef = {
            name = var.catalog_contract.trino_credentials_secret_name
          }
        },
      ]

      catalogs = {
        iceberg = <<-CATALOG
          # openlakeforge.polaris-bootstrap-run=${var.catalog_contract.bootstrap_run_id}
          # openlakeforge.polaris-bootstrap-revision=${var.catalog_bootstrap_revision}
          connector.name=iceberg
          iceberg.catalog.type=rest
          iceberg.rest-catalog.uri=${var.catalog_contract.rest_uri}
          iceberg.rest-catalog.warehouse=${var.catalog_contract.warehouse}
          iceberg.rest-catalog.security=OAUTH2
          iceberg.rest-catalog.oauth2.credential=$${ENV:POLARIS_TRINO_CLIENT_ID}:$${ENV:POLARIS_TRINO_CLIENT_SECRET}
          iceberg.rest-catalog.oauth2.server-uri=${var.catalog_contract.token_uri}
          iceberg.rest-catalog.oauth2.scope=${var.catalog_contract.oauth_scope}
          iceberg.rest-catalog.vended-credentials-enabled=false
          iceberg.rest-catalog.nested-namespace-enabled=true
          fs.native-s3.enabled=true
          s3.endpoint=${var.storage_contract.endpoint}
          s3.path-style-access=${var.storage_contract.path_style_access}
          s3.region=${var.storage_contract.region}
          s3.aws-access-key=$${ENV:AWS_ACCESS_KEY_ID}
          s3.aws-secret-key=$${ENV:AWS_SECRET_ACCESS_KEY}
        CATALOG
      }
    }),
  ]
}
