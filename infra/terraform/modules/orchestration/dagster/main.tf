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
            }
          }
        }
      }
    }),
  ]
}
