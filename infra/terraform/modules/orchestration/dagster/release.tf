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
        # User code is a stage activation, not Terraform state. The two keys
        # are not interchangeable: `enabled` is what makes the parent chart
        # render workspace.yaml at all, and turning it off while the subchart
        # condition is still true trips the chart's own
        # "subchart cannot be enabled if workspace.yaml is not created" guard.
        # Only `enableSubchart` moves the deployments themselves out, into the
        # openlakeforge-project release that activation installs after its
        # revision verifies.
        enabled        = true
        enableSubchart = false
      }

      # The webserver and daemon images stay where the catalog pins them, in
      # `base_values_file`: overriding them with a project-code reference here
      # is what made a platform apply depend on a project build existing.
      dagsterWebserver = {
        # dagster-user-deployments names each Service after its deployment, so
        # the contract's code-location name is also its in-cluster host.
        workspace = {
          enabled = true
          servers = [
            for location in var.code_locations : {
              host = location.name
              port = 3030
              name = location.name
            }
          ]
        }
        env        = local.runtime_env
        envSecrets = local.runtime_env_secrets
      }

      serviceAccount = {
        annotations = var.service_account_annotations
      }

      dagsterDaemon = {
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
            # Deliberately no `image`: Dagster then launches runs with the code
            # location's own container image, which stage activation sets to the
            # activated revision's digest. A value here would be captured at
            # platform-apply time and silently outrank every later promotion.
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
