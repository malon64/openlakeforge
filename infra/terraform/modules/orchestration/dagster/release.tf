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
        # User code is a stage activation, not Terraform state.  The stable
        # endpoint is served by the separately released openlakeforge-project
        # chart, which activation installs only after its revision verifies.
        enabled = false
      }

      dagsterWebserver = {
        image = {
          repository = var.project_code_image_repository
          tag        = var.project_code_image_tag
          pullPolicy = var.project_code_image_pull_policy
        }
        workspace = {
          enabled = true
          servers = [
            {
              host = "openlakeforge-project-dagster-user-deployments"
              port = 3030
              name = "openlakeforge-dagster"
            },
          ]
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
            bucket = var.artifact_bucket_name
            # A stage's own storage identity is scoped to
            # activations/<stage>/* in the shared ops bucket (#114) - a
            # bucket-root "logs/dagster/compute" prefix falls outside that
            # grant and every compute-log upload is denied. Compute-log
            # calls carry no other stage's activation prefix, so deriving it
            # from log_base_uri (already stage-scoped) keeps this instance's
            # logs inside its own grant.
            localDir       = "/tmp/dagster-compute-logs"
            prefix         = "${trimsuffix(replace(var.log_base_uri, "s3://${var.artifact_bucket_name}/", ""), "/")}/dagster/compute"
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
