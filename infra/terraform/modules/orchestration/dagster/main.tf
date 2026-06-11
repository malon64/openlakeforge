locals {
  catalog_type     = coalesce(try(var.catalog_contract.catalog_type, null), "rest")
  catalog_provider = coalesce(try(var.catalog_contract.catalog_provider, null), "polaris")
  catalog_name     = coalesce(try(var.catalog_contract.catalog_name, null), try(var.catalog_contract.warehouse, null), "sales_dev")
  runtime_profile  = coalesce(try(var.catalog_contract.runtime_profile, null), "polaris-rest")

  storage_env = concat(
    [
      {
        name  = "AWS_REGION"
        value = var.storage_contract.region
      },
      {
        name  = "AWS_DEFAULT_REGION"
        value = var.storage_contract.region
      },
    ],
    var.storage_contract.endpoint == null ? [] : [
      {
        name  = "AWS_ENDPOINT_URL_S3"
        value = var.storage_contract.endpoint
      },
      {
        name  = "OPENLAKEFORGE_DUCKDB_S3_ENDPOINT"
        value = replace(var.storage_contract.endpoint, "http://", "")
      },
    ],
    var.storage_contract.path_style_access == null ? [] : [
      {
        name  = "AWS_S3_FORCE_PATH_STYLE"
        value = tostring(var.storage_contract.path_style_access)
      },
    ],
    try(var.storage_contract.ssl_mode, null) == "disabled" ? [
      {
        name  = "AWS_ALLOW_HTTP"
        value = "true"
      },
    ] : [],
    [
      {
        name  = "OPENLAKEFORGE_S3_BUCKET"
        value = var.storage_contract.bucket_name
      },
    ],
  )

  artifact_env = [
    {
      name  = "OPENLAKEFORGE_FLOE_MANIFEST_URI"
      value = var.floe_manifest_uri
    },
    {
      name  = "OPENLAKEFORGE_FLOE_MANIFEST_REVISION"
      value = var.floe_manifest_revision
    },
    {
      name  = "OPENLAKEFORGE_PROJECT_CODE_REVISION"
      value = var.project_code_image_revision
    },
  ]

  generic_catalog_env = [
    {
      name  = "OPENLAKEFORGE_CATALOG_TYPE"
      value = local.catalog_type
    },
    {
      name  = "OPENLAKEFORGE_CATALOG_PROVIDER"
      value = local.catalog_provider
    },
    {
      name  = "OPENLAKEFORGE_CATALOG_NAME"
      value = local.catalog_name
    },
    {
      name  = "OPENLAKEFORGE_CATALOG_RUNTIME_PROFILE"
      value = local.runtime_profile
    },
  ]

  polaris_catalog_env = local.catalog_type == "rest" ? [
    {
      name  = "POLARIS_REST_URI"
      value = coalesce(try(var.catalog_contract.rest_uri, null), "")
    },
    {
      name  = "POLARIS_TOKEN_URI"
      value = coalesce(try(var.catalog_contract.token_uri, null), "")
    },
    {
      name  = "POLARIS_WAREHOUSE"
      value = coalesce(try(var.catalog_contract.warehouse, null), local.catalog_name)
    },
    {
      name  = "POLARIS_OAUTH_SCOPE"
      value = coalesce(try(var.catalog_contract.oauth_scope, null), "")
    },
  ] : []

  dbt_env = [
    {
      name  = "DBT_PROFILES_DIR"
      value = "/opt/openlakeforge/domains/sales/transformations/dbt"
    },
    {
      name  = "OPENLAKEFORGE_DBT_ATTACH_POLARIS"
      value = tostring(local.catalog_type == "rest" && local.catalog_provider == "polaris")
    },
  ]

  runtime_env = concat(local.storage_env, local.artifact_env, local.generic_catalog_env, local.polaris_catalog_env, local.dbt_env)

  runtime_env_secrets = concat(
    var.storage_contract.credentials_secret_name == null ? [] : [
      {
        name = var.storage_contract.credentials_secret_name
      },
    ],
    try(var.catalog_contract.floe_credentials_secret_name, null) == null ? [] : [
      {
        name = var.catalog_contract.floe_credentials_secret_name
      },
    ],
    try(var.catalog_contract.dbt_credentials_secret_name, null) == null ? [] : [
      {
        name = var.catalog_contract.dbt_credentials_secret_name
      },
    ],
  )
}

resource "helm_release" "dagster" {
  name       = var.release_name
  repository = var.chart_repository
  chart      = "dagster"
  version    = var.chart_version
  namespace  = var.namespace

  # Local project code is a dynamic artifact loaded after Terraform. Do not make
  # static infra apply wait on pods that cannot start until that image exists.
  wait            = false
  timeout         = 300
  cleanup_on_fail = true

  values = [
    file(var.base_values_file),
    yamlencode({
      global = {
        serviceAccountName   = "dagster"
        postgresqlSecretName = var.postgresql_contract.dagster_credentials_secret_name
      }

      generatePostgresqlPasswordSecret = false

      postgresql = {
        enabled            = false
        postgresqlHost     = var.postgresql_contract.host
        postgresqlPort     = tostring(var.postgresql_contract.port)
        postgresqlDatabase = var.postgresql_contract.dagster_db_name
        postgresqlUsername = var.postgresql_contract.dagster_db_user
      }

      "dagster-user-deployments" = {
        enabled        = true
        enableSubchart = true
        deployments = [
          {
            name = var.code_location_name
            image = {
              repository = var.project_code_image_repository
              tag        = var.project_code_image_tag
              pullPolicy = var.project_code_image_pull_policy
            }
            dagsterApiGrpcArgs = [
              "--module-name",
              var.definitions_module,
            ]
            port = 3030
            includeConfigInLaunchedRuns = {
              enabled = true
            }
            deploymentConfig = {
              strategy = {
                type = "Recreate"
              }
            }
            podSpecConfig = {
              terminationGracePeriodSeconds = 10
            }
            env        = local.runtime_env
            envSecrets = local.runtime_env_secrets
          },
        ]
      }

      dagsterWebserver = {
        image = {
          repository = var.project_code_image_repository
          tag        = var.project_code_image_tag
          pullPolicy = var.project_code_image_pull_policy
        }
      }

      dagsterDaemon = {
        image = {
          repository = var.project_code_image_repository
          tag        = var.project_code_image_tag
          pullPolicy = var.project_code_image_pull_policy
        }
      }

      runLauncher = {
        type = "K8sRunLauncher"
        config = {
          k8sRunLauncher = {
            imagePullPolicy = var.project_code_image_pull_policy
            image = {
              repository = var.project_code_image_repository
              tag        = var.project_code_image_tag
              pullPolicy = var.project_code_image_pull_policy
            }
            jobNamespace        = var.namespace
            loadInclusterConfig = true
            failPodOnRunFailure = true
            runK8sConfig = {
              jobSpecConfig = {
                ttlSecondsAfterFinished = 3600
              }
              containerConfig = {
                env = local.runtime_env
                envFrom = [
                  for secret in local.runtime_env_secrets : {
                    secretRef = {
                      name = secret.name
                    }
                  }
                ]
              }
            }
          }
        }
      }
    }),
  ]
}
