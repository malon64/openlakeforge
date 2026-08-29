resource "kubernetes_job_v1" "bootstrap" {
  metadata {
    name      = "openmetadata-bootstrap-${helm_release.openmetadata.metadata.revision}"
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
          args = [templatefile("${path.module}/templates/bootstrap.sh.tftpl", {
            om_url                        = local.om_url
            admin_email                   = var.admin_email
            admin_password                = var.admin_password
            catalog_type                  = local.catalog_type
            catalog_service_name          = local.catalog_service_name
            catalog_service_display_name  = local.catalog_service_display_name
            catalog_uri                   = coalesce(try(var.catalog_contract.rest_uri, null), try(var.catalog_contract.glue_rest_uri, null), "")
            catalog_warehouse             = coalesce(try(var.catalog_contract.warehouse, null), try(var.catalog_contract.glue_rest_warehouse, null), var.catalog_database_name)
            token_uri                     = (try(var.catalog_contract.token_uri, null) == null ? "" : try(var.catalog_contract.token_uri, null))
            oauth_scope                   = (try(var.catalog_contract.oauth_scope, null) == null ? "" : try(var.catalog_contract.oauth_scope, null))
            ingestion_bot_secret_name     = var.ingestion_bot_secret_name
            trino_lineage_namespace       = var.trino_lineage_namespace
            ingestion_bot_jwt_key         = var.ingestion_bot_jwt_key
            storage_region                = var.storage_contract.region
            storage_endpoint              = (try(var.storage_contract.virtual_host_endpoint, null) == null ? "" : try(var.storage_contract.virtual_host_endpoint, null))
            catalog_database_name         = var.catalog_database_name
            catalog_database_fqn          = local.catalog_database_fqn
            catalog_schema_names_json_b64 = local.catalog_schema_names_json_b64
            dagster_webserver_url         = var.dagster_webserver_url
            superset_url                  = var.superset_url
            superset_username             = var.superset_username
            superset_password             = var.superset_password
            superset_auth_provider        = var.superset_auth_provider
            superset_verify_ssl           = var.superset_verify_ssl
          })]

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
