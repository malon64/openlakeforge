# OpenSearch via the openmetadata-dependencies chart (Airflow + MySQL disabled)
resource "helm_release" "openmetadata_deps" {
  name       = "${var.release_name}-deps"
  repository = var.chart_repository
  chart      = "openmetadata-dependencies"
  version    = var.deps_chart_version
  namespace  = var.namespace

  wait    = true
  timeout = 1200

  values = [
    file(var.deps_values_file),
  ]
}

# Main OpenMetadata application
resource "helm_release" "openmetadata" {
  name       = var.release_name
  repository = var.chart_repository
  chart      = "openmetadata"
  version    = var.chart_version
  namespace  = var.namespace

  wait            = true
  timeout         = 600
  cleanup_on_fail = true
  upgrade_install = true

  values = [
    file(var.base_values_file),
    yamlencode({
      openmetadata = {
        config = {
          database = {
            host         = var.postgresql_contract.host
            port         = var.postgresql_contract.port
            driverClass  = "org.postgresql.Driver"
            dbScheme     = "postgresql"
            databaseName = var.postgresql_contract.openmetadata_db_name
            auth = {
              username = var.postgresql_contract.openmetadata_db_user
              password = {
                secretRef = var.postgresql_contract.openmetadata_credentials_secret_name
                secretKey = "postgresql-password"
              }
            }
            dbParams = "sslmode=${local.postgresql_ssl_mode}"
          }
          pipelineServiceClientConfig = {
            k8s = {
              namespace = var.namespace
            }
          }
        }
      }
    }),
  ]

  depends_on = [
    helm_release.openmetadata_deps,
  ]
}

resource "terraform_data" "openmetadata_release_revision" {
  triggers_replace = [
    helm_release.openmetadata.metadata.revision,
  ]
}

resource "terraform_data" "openmetadata_catalog_schemas" {
  triggers_replace = [
    var.catalog_database_name,
    sha256(local.catalog_schema_names_json),
  ]
}
