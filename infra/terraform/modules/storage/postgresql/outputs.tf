output "contract" {
  description = "Shared PostgreSQL contract consumed by local platform modules."
  value = {
    host = local.host
    port = local.port

    dagster_db_name                 = var.dagster_db_name
    dagster_db_user                 = var.dagster_db_user
    dagster_credentials_secret_name = var.dagster_credentials_secret_name

    openmetadata_db_name                 = var.openmetadata_db_name
    openmetadata_db_user                 = var.openmetadata_db_user
    openmetadata_credentials_secret_name = var.openmetadata_credentials_secret_name

    superset_db_name                 = var.superset_db_name
    superset_db_user                 = var.superset_db_user
    superset_credentials_secret_name = var.superset_credentials_secret_name
  }

  depends_on = [
    kubernetes_stateful_set_v1.postgresql,
    kubernetes_job_v1.bootstrap,
  ]
}
