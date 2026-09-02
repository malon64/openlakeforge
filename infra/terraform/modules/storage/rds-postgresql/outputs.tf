output "contract" {
  description = "Metadata PostgreSQL contract implemented by AWS RDS PostgreSQL. Mirrors modules/storage/postgresql's contract shape."
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
  }

  depends_on = [
    aws_db_instance.this,
    kubernetes_job_v1.bootstrap,
  ]
}
