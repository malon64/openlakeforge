output "contract" {
  description = "Metadata PostgreSQL contract implemented by the local in-cluster database."
  value = {
    host = local.host
    port = local.port

    dagster_db_name                 = var.dagster_db_name
    dagster_db_user                 = var.dagster_db_user
    dagster_credentials_secret_name = var.dagster_credentials_secret_name

    openmetadata_db_name                 = var.enable_openmetadata ? var.openmetadata_db_name : null
    openmetadata_db_user                 = var.enable_openmetadata ? var.openmetadata_db_user : null
    openmetadata_credentials_secret_name = var.enable_openmetadata ? var.openmetadata_credentials_secret_name : null

    superset_db_name                 = var.enable_superset ? var.superset_db_name : null
    superset_db_user                 = var.enable_superset ? var.superset_db_user : null
    superset_credentials_secret_name = var.enable_superset ? var.superset_credentials_secret_name : null

    polaris_credentials_secret_name = var.polaris_credentials_secret_name
  }

  depends_on = [
    kubernetes_stateful_set_v1.postgresql,
    kubernetes_job_v1.bootstrap,
  ]
}
