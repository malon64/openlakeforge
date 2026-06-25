output "contract" {
  description = "Metadata PostgreSQL contract implemented by AWS RDS PostgreSQL."
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
    kubernetes_job_v1.bootstrap,
  ]
}
