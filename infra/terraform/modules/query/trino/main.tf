locals {
  chart                = var.chart_package_path != null ? var.chart_package_path : "trino"
  repository           = var.chart_package_path != null ? null : var.chart_repository
  version              = var.chart_package_path != null ? null : var.chart_version
  iceberg_catalog_type = coalesce(try(var.catalog_contract.catalog_type, null), "rest")
  trino_catalog_name   = coalesce(try(var.catalog_contract.trino_catalog_name, null), "iceberg")
  glue_region          = coalesce(try(var.catalog_contract.glue_region, null), var.storage_contract.region)

  storage_secret_env_from = var.storage_contract.credentials_secret_name == null ? [] : [
    {
      secretRef = {
        name = var.storage_contract.credentials_secret_name
      }
    },
  ]

  catalog_secret_env_from = try(var.catalog_contract.trino_credentials_secret_name, null) == null ? [] : [
    {
      secretRef = {
        name = var.catalog_contract.trino_credentials_secret_name
      }
    },
  ]

  catalog_secret_env = try(var.catalog_contract.trino_credentials_secret_name, null) == null ? [] : [
    {
      name = "OPENLAKEFORGE_CATALOG_TRINO_CLIENT_ID"
      valueFrom = {
        secretKeyRef = {
          name = var.catalog_contract.trino_credentials_secret_name
          key  = coalesce(try(var.catalog_contract.trino_client_id_key, null), "POLARIS_TRINO_CLIENT_ID")
        }
      }
    },
    {
      name = "OPENLAKEFORGE_CATALOG_TRINO_CLIENT_SECRET"
      valueFrom = {
        secretKeyRef = {
          name = var.catalog_contract.trino_credentials_secret_name
          key  = coalesce(try(var.catalog_contract.trino_client_secret_key, null), "POLARIS_TRINO_CLIENT_SECRET")
        }
      }
    },
  ]

  s3_catalog_properties = join("\n", compact([
    "fs.native-s3.enabled=true",
    var.storage_contract.endpoint == null ? "" : "s3.endpoint=${var.storage_contract.endpoint}",
    var.storage_contract.path_style_access == null ? "" : "s3.path-style-access=${var.storage_contract.path_style_access}",
    "s3.region=${var.storage_contract.region}",
    var.storage_contract.credentials_secret_name == null ? "" : "s3.aws-access-key=$${ENV:AWS_ACCESS_KEY_ID}",
    var.storage_contract.credentials_secret_name == null ? "" : "s3.aws-secret-key=$${ENV:AWS_SECRET_ACCESS_KEY}",
  ]))

  rest_iceberg_catalog = <<-CATALOG
    # openlakeforge.catalog-provider=${coalesce(try(var.catalog_contract.catalog_provider, null), "polaris")}
    # openlakeforge.polaris-bootstrap-run=${(try(var.catalog_contract.bootstrap_run_id, null) == null ? "" : try(var.catalog_contract.bootstrap_run_id, null))}
    # openlakeforge.polaris-bootstrap-revision=${var.catalog_bootstrap_revision}
    connector.name=iceberg
    iceberg.catalog.type=rest
    iceberg.rest-catalog.uri=${(try(var.catalog_contract.rest_uri, null) == null ? "" : try(var.catalog_contract.rest_uri, null))}
    iceberg.rest-catalog.warehouse=${(try(var.catalog_contract.warehouse, null) == null ? "" : try(var.catalog_contract.warehouse, null))}
    iceberg.rest-catalog.security=OAUTH2
    iceberg.rest-catalog.oauth2.credential=$${ENV:OPENLAKEFORGE_CATALOG_TRINO_CLIENT_ID}:$${ENV:OPENLAKEFORGE_CATALOG_TRINO_CLIENT_SECRET}
    iceberg.rest-catalog.oauth2.server-uri=${(try(var.catalog_contract.token_uri, null) == null ? "" : try(var.catalog_contract.token_uri, null))}
    iceberg.rest-catalog.oauth2.scope=${(try(var.catalog_contract.oauth_scope, null) == null ? "" : try(var.catalog_contract.oauth_scope, null))}
    iceberg.rest-catalog.vended-credentials-enabled=false
    iceberg.rest-catalog.nested-namespace-enabled=true
    ${local.s3_catalog_properties}
  CATALOG

  glue_iceberg_catalog = <<-CATALOG
    # openlakeforge.catalog-provider=aws-glue
    connector.name=iceberg
    iceberg.catalog.type=glue
    hive.metastore.glue.region=${local.glue_region}
    ${local.s3_catalog_properties}
  CATALOG

  iceberg_catalog_properties = local.iceberg_catalog_type == "glue" ? local.glue_iceberg_catalog : local.rest_iceberg_catalog
  # Create a named service account when one is explicitly requested (EKS Pod Identity
  # binds credentials by SA name and needs no annotation) or, IRSA-style, when
  # annotations are supplied. Otherwise Trino runs under the namespace default SA.
  service_account_name   = var.service_account_name != "" ? var.service_account_name : "trino"
  create_service_account = var.service_account_name != "" || length(var.service_account_annotations) > 0
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
      envFrom = concat(local.storage_secret_env_from, local.catalog_secret_env_from)
      env     = local.catalog_secret_env

      catalogs = {
        (local.trino_catalog_name) = local.iceberg_catalog_properties
      }

      serviceAccount = {
        create      = local.create_service_account
        name        = local.create_service_account ? local.service_account_name : ""
        annotations = var.service_account_annotations
      }
    }),
  ]
}
