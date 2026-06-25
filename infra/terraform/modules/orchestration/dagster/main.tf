locals {
  catalog_type     = coalesce(try(var.catalog_contract.catalog_type, null), "rest")
  catalog_provider = coalesce(try(var.catalog_contract.catalog_provider, null), "polaris")
  catalog_name     = coalesce(try(var.catalog_contract.catalog_name, null), try(var.catalog_contract.warehouse, null), "lakehouse_dev")
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
        name  = "OPENLAKEFORGE_BRONZE_BUCKET"
        value = coalesce(var.storage_contract.bronze_bucket_name, var.storage_contract.bucket_name)
      },
    ],
  )

  artifact_env = [
    {
      name  = "OPENLAKEFORGE_OPS_BUCKET_NAME"
      value = var.artifact_bucket_name
    },
    {
      name  = "OPENLAKEFORGE_ARTIFACT_BUCKET_NAME"
      value = var.artifact_bucket_name
    },
    {
      name  = "OPENLAKEFORGE_ARTIFACT_BASE_URI"
      value = var.artifact_base_uri
    },
    {
      name  = "OPENLAKEFORGE_FLOE_MANIFEST_ACCESS_MODE"
      value = var.floe_manifest_access_mode
    },
    {
      name  = "OPENLAKEFORGE_FLOE_MANIFEST_BASE_URI"
      value = var.floe_manifest_base_uri
    },
    {
      name  = "OPENLAKEFORGE_FLOE_REPORT_BASE_URI"
      value = var.floe_report_base_uri
    },
    {
      name  = "OPENLAKEFORGE_LOG_BASE_URI"
      value = var.log_base_uri
    },
    {
      name  = "OPENLAKEFORGE_RUN_ARTIFACT_BASE_URI"
      value = var.run_artifact_base_uri
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
    {
      name  = "OPENLAKEFORGE_CATALOG_REST_URI"
      value = coalesce(try(var.catalog_contract.rest_uri, null), "")
    },
    {
      name  = "OPENLAKEFORGE_CATALOG_TOKEN_URI"
      value = coalesce(try(var.catalog_contract.token_uri, null), "")
    },
    {
      name  = "OPENLAKEFORGE_CATALOG_WAREHOUSE"
      value = coalesce(try(var.catalog_contract.warehouse, null), local.catalog_name)
    },
    {
      name  = "OPENLAKEFORGE_CATALOG_OAUTH_SCOPE"
      value = coalesce(try(var.catalog_contract.oauth_scope, null), "")
    },
  ]

  glue_catalog_env = local.catalog_type == "glue" ? [
    {
      name  = "OPENLAKEFORGE_CATALOG_GLUE_REGION"
      value = coalesce(try(var.catalog_contract.glue_region, null), var.storage_contract.region)
    },
    {
      name  = "OPENLAKEFORGE_CATALOG_GLUE_CATALOG_ID"
      value = coalesce(try(var.catalog_contract.glue_catalog_id, null), "")
    },
    {
      name  = "OPENLAKEFORGE_CATALOG_GLUE_REST_URI"
      value = coalesce(try(var.catalog_contract.glue_rest_uri, null), try(var.catalog_contract.rest_uri, null), "")
    },
    {
      name  = "OPENLAKEFORGE_DBT_TARGET"
      value = "aws_runtime"
    },
    ] : [
    {
      name  = "OPENLAKEFORGE_DBT_TARGET"
      value = "local_runtime"
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
      value = "/opt/openlakeforge/domains"
    },
    {
      name  = "OPENLAKEFORGE_DBT_ATTACH_POLARIS"
      value = tostring(local.catalog_type == "rest" && local.catalog_provider == "polaris")
    },
    {
      name  = "OPENLAKEFORGE_POSTGRES_SSL_MODE"
      value = var.postgresql_ssl_mode
    },
  ]

  dbt_secret_env = try(var.catalog_contract.dbt_credentials_secret_name, null) == null ? [] : [
    {
      name = "OPENLAKEFORGE_CATALOG_DBT_CLIENT_ID"
      valueFrom = {
        secretKeyRef = {
          name = var.catalog_contract.dbt_credentials_secret_name
          key  = coalesce(try(var.catalog_contract.dbt_client_id_key, null), "POLARIS_DBT_CLIENT_ID")
        }
      }
    },
    {
      name = "OPENLAKEFORGE_CATALOG_DBT_CLIENT_SECRET"
      valueFrom = {
        secretKeyRef = {
          name = var.catalog_contract.dbt_credentials_secret_name
          key  = coalesce(try(var.catalog_contract.dbt_client_secret_key, null), "POLARIS_DBT_CLIENT_SECRET")
        }
      }
    },
  ]

  runtime_env = concat(local.storage_env, local.artifact_env, local.generic_catalog_env, local.glue_catalog_env, local.polaris_catalog_env, local.dbt_env, local.dbt_secret_env)

  log_archive_env = concat(
    local.storage_env,
    local.artifact_env,
    [
      {
        name  = "OPENLAKEFORGE_KUBE_NAMESPACE"
        value = var.namespace
      },
      {
        name  = "OPENLAKEFORGE_LOG_ARCHIVE_SINCE_SECONDS"
        value = "3600"
      },
    ],
  )

  code_location_deployments = [
    for location in var.code_locations : {
      name = location.name
      image = {
        repository = var.project_code_image_repository
        tag        = var.project_code_image_tag
        pullPolicy = var.project_code_image_pull_policy
      }
      dagsterApiGrpcArgs = [
        "--module-name",
        location.definitions_module,
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
    }
  ]

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
  repository = var.chart_package_path == null ? var.chart_repository : null
  chart      = var.chart_package_path == null ? "dagster" : var.chart_package_path
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
        serviceAccount = {
          annotations = var.service_account_annotations
        }
        deployments = local.code_location_deployments
      }

      dagsterWebserver = {
        image = {
          repository = var.project_code_image_repository
          tag        = var.project_code_image_tag
          pullPolicy = var.project_code_image_pull_policy
        }
        env        = local.runtime_env
        envSecrets = local.runtime_env_secrets
      }

      serviceAccount = {
        annotations = var.service_account_annotations
      }

      dagsterDaemon = {
        image = {
          repository = var.project_code_image_repository
          tag        = var.project_code_image_tag
          pullPolicy = var.project_code_image_pull_policy
        }
        env        = local.runtime_env
        envSecrets = local.runtime_env_secrets
      }

      computeLogManager = {
        type = "S3ComputeLogManager"
        config = {
          s3ComputeLogManager = {
            bucket         = var.artifact_bucket_name
            localDir       = "/tmp/dagster-compute-logs"
            prefix         = "logs/dagster/compute"
            useSsl         = try(var.storage_contract.ssl_mode, null) != "disabled"
            verify         = try(var.storage_contract.ssl_mode, null) != "disabled"
            endpointUrl    = var.storage_contract.endpoint
            region         = var.storage_contract.region
            skipEmptyFiles = true
          }
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
              podSpecConfig = {
                serviceAccountName = "dagster"
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

resource "kubernetes_cron_job_v1" "kubernetes_log_archive" {
  metadata {
    name      = "openlakeforge-k8s-log-archive"
    namespace = var.namespace
    labels = {
      "app.kubernetes.io/name"       = "openlakeforge-k8s-log-archive"
      "app.kubernetes.io/managed-by" = "terraform"
      "openlakeforge.io/component"   = "observability"
    }
  }

  spec {
    schedule                      = var.kubernetes_log_archive_schedule
    concurrency_policy            = "Forbid"
    successful_jobs_history_limit = 1
    failed_jobs_history_limit     = 3

    job_template {
      metadata {
        labels = {
          "app.kubernetes.io/name"     = "openlakeforge-k8s-log-archive"
          "openlakeforge.io/component" = "observability"
        }
      }

      spec {
        backoff_limit = 1

        template {
          metadata {
            labels = {
              "app.kubernetes.io/name"     = "openlakeforge-k8s-log-archive"
              "openlakeforge.io/component" = "observability"
            }
          }

          spec {
            service_account_name            = "dagster"
            automount_service_account_token = true
            restart_policy                  = "Never"

            container {
              name              = "archive-k8s-logs"
              image             = "${var.project_code_image_repository}:${var.project_code_image_tag}"
              image_pull_policy = var.project_code_image_pull_policy
              command           = ["python", "-m", "libs.k8s_log_archive"]

              dynamic "env" {
                for_each = local.log_archive_env
                content {
                  name  = env.value.name
                  value = env.value.value
                }
              }

              dynamic "env_from" {
                for_each = var.storage_contract.credentials_secret_name == null ? [] : [var.storage_contract.credentials_secret_name]
                content {
                  secret_ref {
                    name = env_from.value
                  }
                }
              }
            }
          }
        }
      }
    }
  }

  depends_on = [
    helm_release.dagster,
  ]
}
