resource "helm_release" "dagster" {
  name       = var.release_name
  repository = var.chart_repository
  chart      = "dagster"
  version    = var.chart_version
  namespace  = var.namespace

  wait    = true
  timeout = 300

  values = [
    file(var.base_values_file),
    yamlencode({
      global = {
        serviceAccountName = "dagster"
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
            env = [
              {
                name  = "AWS_REGION"
                value = var.storage_contract.region
              },
              {
                name  = "AWS_DEFAULT_REGION"
                value = var.storage_contract.region
              },
              {
                name  = "AWS_ENDPOINT_URL_S3"
                value = var.storage_contract.endpoint
              },
              {
                name  = "OPENLAKEFORGE_DUCKDB_S3_ENDPOINT"
                value = replace(var.storage_contract.endpoint, "http://", "")
              },
              {
                name  = "AWS_S3_FORCE_PATH_STYLE"
                value = tostring(var.storage_contract.path_style_access)
              },
              {
                name  = "AWS_ALLOW_HTTP"
                value = "true"
              },
              {
                name  = "OPENLAKEFORGE_S3_BUCKET"
                value = var.storage_contract.bucket_name
              },
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
              {
                name  = "POLARIS_REST_URI"
                value = var.catalog_contract.rest_uri
              },
              {
                name  = "POLARIS_TOKEN_URI"
                value = var.catalog_contract.token_uri
              },
              {
                name  = "POLARIS_WAREHOUSE"
                value = var.catalog_contract.warehouse
              },
              {
                name  = "POLARIS_OAUTH_SCOPE"
                value = var.catalog_contract.oauth_scope
              },
              {
                name  = "DBT_PROFILES_DIR"
                value = "/opt/openlakeforge/domains/sales/transformations/dbt"
              },
              {
                name  = "OPENLAKEFORGE_DBT_ATTACH_POLARIS"
                value = "true"
              },
            ]
            envSecrets = [
              {
                name = var.storage_contract.credentials_secret_name
              },
              {
                name = var.catalog_contract.floe_credentials_secret_name
              },
              {
                name = var.catalog_contract.dbt_credentials_secret_name
              },
            ]
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
                env = [
                  {
                    name  = "AWS_REGION"
                    value = var.storage_contract.region
                  },
                  {
                    name  = "AWS_DEFAULT_REGION"
                    value = var.storage_contract.region
                  },
                  {
                    name  = "AWS_ENDPOINT_URL_S3"
                    value = var.storage_contract.endpoint
                  },
                  {
                    name  = "OPENLAKEFORGE_DUCKDB_S3_ENDPOINT"
                    value = replace(var.storage_contract.endpoint, "http://", "")
                  },
                  {
                    name  = "AWS_S3_FORCE_PATH_STYLE"
                    value = tostring(var.storage_contract.path_style_access)
                  },
                  {
                    name  = "AWS_ALLOW_HTTP"
                    value = "true"
                  },
                  {
                    name  = "OPENLAKEFORGE_S3_BUCKET"
                    value = var.storage_contract.bucket_name
                  },
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
                  {
                    name  = "POLARIS_REST_URI"
                    value = var.catalog_contract.rest_uri
                  },
                  {
                    name  = "POLARIS_TOKEN_URI"
                    value = var.catalog_contract.token_uri
                  },
                  {
                    name  = "POLARIS_WAREHOUSE"
                    value = var.catalog_contract.warehouse
                  },
                  {
                    name  = "POLARIS_OAUTH_SCOPE"
                    value = var.catalog_contract.oauth_scope
                  },
                  {
                    name  = "DBT_PROFILES_DIR"
                    value = "/opt/openlakeforge/domains/sales/transformations/dbt"
                  },
                  {
                    name  = "OPENLAKEFORGE_DBT_ATTACH_POLARIS"
                    value = "true"
                  },
                ]
                envFrom = [
                  {
                    secretRef = {
                      name = var.storage_contract.credentials_secret_name
                    }
                  },
                  {
                    secretRef = {
                      name = var.catalog_contract.floe_credentials_secret_name
                    }
                  },
                  {
                    secretRef = {
                      name = var.catalog_contract.dbt_credentials_secret_name
                    }
                  },
                ]
              }
            }
          }
        }
      }
    }),
  ]
}
