locals {
  chart      = var.chart_package_path != null ? var.chart_package_path : "trino"
  repository = var.chart_package_path != null ? null : var.chart_repository
  version    = var.chart_package_path != null ? null : var.chart_version
  stage_catalog_contracts = length(var.stage_catalog_contracts) > 0 ? var.stage_catalog_contracts : {
    dev = var.catalog_contract
  }

  storage_secret_env_from = var.storage_contract.credentials_secret_name == null ? [] : [
    {
      secretRef = {
        name = var.storage_contract.credentials_secret_name
      }
    },
  ]

  catalog_secret_env = flatten([
    for stage, contract in local.stage_catalog_contracts : try(contract.trino_credentials_secret_name, null) == null ? [] : [
      {
        name = "OPENLAKEFORGE_CATALOG_${upper(stage)}_CLIENT_ID"
        valueFrom = {
          secretKeyRef = {
            name = contract.trino_credentials_secret_name
            key  = coalesce(try(contract.trino_client_id_key, null), "POLARIS_TRINO_CLIENT_ID")
          }
        }
      },
      {
        name = "OPENLAKEFORGE_CATALOG_${upper(stage)}_CLIENT_SECRET"
        valueFrom = {
          secretKeyRef = {
            name = contract.trino_credentials_secret_name
            key  = coalesce(try(contract.trino_client_secret_key, null), "POLARIS_TRINO_CLIENT_SECRET")
          }
        }
      },
    ]
  ])

  s3_catalog_properties = join("\n", compact([
    "fs.native-s3.enabled=true",
    var.storage_contract.endpoint == null ? "" : "s3.endpoint=${var.storage_contract.endpoint}",
    var.storage_contract.path_style_access == null ? "" : "s3.path-style-access=${var.storage_contract.path_style_access}",
    "s3.region=${var.storage_contract.region}",
    var.storage_contract.credentials_secret_name == null ? "" : "s3.aws-access-key=$${ENV:AWS_ACCESS_KEY_ID}",
    var.storage_contract.credentials_secret_name == null ? "" : "s3.aws-secret-key=$${ENV:AWS_SECRET_ACCESS_KEY}",
  ]))

  iceberg_catalog_properties = {
    for stage, contract in local.stage_catalog_contracts : contract.catalog_name => (
      try(contract.catalog_type, "rest") == "glue" ? <<-CATALOG
        # openlakeforge.catalog-provider=aws-glue
        connector.name=iceberg
        iceberg.catalog.type=glue
        hive.metastore.glue.region=${coalesce(try(contract.glue_region, null), var.storage_contract.region)}
        hive.metastore.glue.catalogid=${try(contract.glue_catalog_id, "")}
        ${local.s3_catalog_properties}
      CATALOG
      : <<-CATALOG
        # openlakeforge.catalog-provider=${coalesce(try(contract.catalog_provider, null), "polaris")}
        # openlakeforge.polaris-bootstrap-run=${try(contract.bootstrap_run_id, "")}
        # openlakeforge.polaris-bootstrap-revision=${var.catalog_bootstrap_revision}
        connector.name=iceberg
        iceberg.catalog.type=rest
        iceberg.rest-catalog.uri=${try(contract.rest_uri, "")}
        iceberg.rest-catalog.warehouse=${try(contract.warehouse, "")}
        iceberg.rest-catalog.security=OAUTH2
        iceberg.rest-catalog.oauth2.credential=$${ENV:OPENLAKEFORGE_CATALOG_${upper(stage)}_CLIENT_ID}:$${ENV:OPENLAKEFORGE_CATALOG_${upper(stage)}_CLIENT_SECRET}
        iceberg.rest-catalog.oauth2.server-uri=${try(contract.token_uri, "")}
        iceberg.rest-catalog.oauth2.scope=${try(contract.oauth_scope, "")}
        iceberg.rest-catalog.vended-credentials-enabled=false
        iceberg.rest-catalog.nested-namespace-enabled=true
        ${local.s3_catalog_properties}
      CATALOG
    )
  }
  catalog_access_rules = jsonencode({
    catalogs = concat(
      [
        for stage, contract in local.stage_catalog_contracts : {
          user    = "olf-${stage}-runtime"
          catalog = contract.catalog_name
          allow   = "all"
        }
      ],
      [{ catalog = ".*", allow = "none" }],
    )
  })
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
  timeout = 600

  values = [
    file(var.base_values_file),
    yamlencode({
      envFrom = local.storage_secret_env_from
      env     = local.catalog_secret_env

      catalogs = local.iceberg_catalog_properties

      accessControl = {
        type       = "configmap"
        configFile = "rules.json"
        rules = {
          "rules.json" = local.catalog_access_rules
        }
      }

      serviceAccount = {
        create      = local.create_service_account
        name        = local.create_service_account ? local.service_account_name : ""
        annotations = var.service_account_annotations
      }
    }),
  ]
}
