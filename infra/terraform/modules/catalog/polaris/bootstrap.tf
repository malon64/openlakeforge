resource "kubernetes_job_v1" "metastore_bootstrap" {
  metadata {
    name      = "polaris-metastore-bootstrap-${helm_release.polaris.metadata.revision}"
    namespace = var.namespace
    labels    = local.labels
  }

  spec {
    backoff_limit = 3

    template {
      metadata {
        labels = merge(local.labels, {
          "openlakeforge.io/job"       = "catalog-metastore-bootstrap"
          "openlakeforge.io/readiness" = "required"
        })
        annotations = local.bootstrap_annotations
      }

      spec {
        restart_policy = "Never"

        container {
          name  = "metastore-bootstrap"
          image = var.metastore_bootstrap_job_image

          command = ["/bin/sh", "-ec"]
          args = [templatefile("${path.module}/templates/metastore-bootstrap.sh.tftpl", {
            release_name = var.release_name
            oauth_scope  = local.oauth_scope
          })]

          env {
            name  = "POLARIS_PERSISTENCE_TYPE"
            value = "relational-jdbc"
          }

          env {
            name  = "POLARIS_REALM"
            value = local.realm
          }

          env {
            name = "POLARIS_BOOTSTRAP_CREDENTIALS"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.bootstrap_credentials.metadata[0].name
                key  = "POLARIS_BOOTSTRAP_CREDENTIALS"
              }
            }
          }

          env {
            name = "ROOT_CLIENT_ID"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.bootstrap_credentials.metadata[0].name
                key  = "ROOT_CLIENT_ID"
              }
            }
          }

          env {
            name = "ROOT_CLIENT_SECRET"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.bootstrap_credentials.metadata[0].name
                key  = "ROOT_CLIENT_SECRET"
              }
            }
          }

          env {
            name = "QUARKUS_DATASOURCE_USERNAME"
            value_from {
              secret_key_ref {
                name = var.postgresql_contract.polaris_credentials_secret_name
                key  = "username"
              }
            }
          }

          env {
            name = "QUARKUS_DATASOURCE_PASSWORD"
            value_from {
              secret_key_ref {
                name = var.postgresql_contract.polaris_credentials_secret_name
                key  = "password"
              }
            }
          }

          env {
            name = "QUARKUS_DATASOURCE_JDBC_URL"
            value_from {
              secret_key_ref {
                name = var.postgresql_contract.polaris_credentials_secret_name
                key  = "jdbcUrl"
              }
            }
          }
        }
      }
    }
  }

  wait_for_completion = true

  timeouts {
    create = "5m"
    update = "5m"
  }

  depends_on = [
    helm_release.polaris,
  ]

  lifecycle {
    replace_triggered_by = [
      terraform_data.polaris_release_revision,
      terraform_data.polaris_bootstrap_revision,
    ]
  }
}

resource "kubernetes_job_v1" "bootstrap" {
  metadata {
    name      = "polaris-bootstrap-${helm_release.polaris.metadata.revision}"
    namespace = var.namespace
    labels    = local.labels
  }

  spec {
    backoff_limit = 3

    template {
      metadata {
        labels = merge(local.labels, {
          "openlakeforge.io/job"       = "catalog-bootstrap"
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
          args = [templatefile("${path.module}/templates/bootstrap.sh.tftpl", {
            release_name                     = var.release_name
            oauth_scope                      = local.oauth_scope
            bronze_bucket_name               = local.bronze_bucket_name
            silver_bucket_name               = local.silver_bucket_name
            gold_bucket_name                 = local.gold_bucket_name
            catalog_name                     = var.catalog_name
            principal_name                   = var.principal_name
            trino_credentials_secret_name    = var.trino_credentials_secret_name
            principal_role                   = var.principal_role
            catalog_role                     = var.catalog_role
            floe_principal_name              = var.floe_principal_name
            floe_credentials_secret_name     = var.floe_credentials_secret_name
            floe_principal_role              = var.floe_principal_role
            floe_catalog_role                = var.floe_catalog_role
            om_principal_name                = var.om_principal_name
            om_credentials_secret_name       = var.om_credentials_secret_name
            om_principal_role                = var.om_principal_role
            om_catalog_role                  = var.om_catalog_role
            deployer_principal_name          = var.deployer_principal_name
            deployer_credentials_secret_name = var.deployer_credentials_secret_name
            deployer_principal_role          = var.deployer_principal_role
            deployer_catalog_role            = var.deployer_catalog_role
          })]

          env {
            name = "NAMESPACE"
            value_from {
              field_ref {
                field_path = "metadata.namespace"
              }
            }
          }

          env {
            name = "ROOT_CLIENT_ID"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.bootstrap_credentials.metadata[0].name
                key  = "ROOT_CLIENT_ID"
              }
            }
          }

          env {
            name = "ROOT_CLIENT_SECRET"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.bootstrap_credentials.metadata[0].name
                key  = "ROOT_CLIENT_SECRET"
              }
            }
          }
        }
      }
    }
  }

  wait_for_completion = true

  timeouts {
    create = "5m"
    update = "5m"
  }

  depends_on = [
    kubernetes_job_v1.metastore_bootstrap,
    kubernetes_role_binding_v1.bootstrap,
  ]

  lifecycle {
    replace_triggered_by = [
      terraform_data.polaris_release_revision,
      terraform_data.polaris_bootstrap_revision,
    ]
  }
}
