output "contract" {
  description = "Metadata PostgreSQL contract implemented by the local in-cluster database."
  value = {
    host = local.host
    port = local.port

    databases = {
      for database in var.databases : database.key => {
        db_name                 = database.db_name
        db_user                 = database.db_user
        credentials_secret_name = database.credentials_secret_name
      }
    }

    polaris_credentials_secret_name = var.polaris_credentials_secret_name
  }

  depends_on = [
    kubernetes_stateful_set_v1.postgresql,
    kubernetes_job_v1.bootstrap,
  ]
}
