resource "kubernetes_job_v1" "bootstrap" {
  metadata {
    name      = local.bootstrap_job_name
    namespace = var.namespace
    labels    = local.labels
  }

  spec {
    backoff_limit = 3

    template {
      metadata {
        labels = merge(local.labels, {
          "openlakeforge.io/job"       = "governance-bootstrap"
          "openlakeforge.io/readiness" = "required"
        })
        annotations = local.bootstrap_annotations
      }

      spec {
        restart_policy       = "Never"
        service_account_name = kubernetes_service_account_v1.bootstrap.metadata[0].name

        container {
          name  = "bootstrap"
          image = var.bootstrap_job_image

          command = ["/bin/sh", "-ec"]
          args    = [local.bootstrap_script]

          env {
            name = "NAMESPACE"
            value_from {
              field_ref {
                field_path = "metadata.namespace"
              }
            }
          }

          dynamic "env" {
            for_each = local.bootstrap_secret_env

            content {
              name = env.value.name
              value_from {
                secret_key_ref {
                  name = env.value.secret_name
                  key  = env.value.key
                }
              }
            }
          }
        }
      }
    }
  }

  wait_for_completion = true

  timeouts {
    create = "15m"
    update = "15m"
  }

  depends_on = [
    helm_release.openmetadata,
    kubernetes_role_binding_v1.bootstrap,
  ]

  lifecycle {
    replace_triggered_by = [
      terraform_data.openmetadata_release_revision,
      terraform_data.openmetadata_catalog_schemas,
    ]
  }
}
