resource "random_password" "postgres_admin" {
  length  = 32
  special = false
}

resource "random_password" "polaris" {
  length  = 32
  special = false
}

resource "kubernetes_secret_v1" "admin_credentials" {
  metadata {
    name      = "${var.release_name}-admin-creds"
    namespace = var.namespace
    labels    = local.labels
  }
  data = {
    "postgres-password" = random_password.postgres_admin.result
  }
  type = "Opaque"
}

resource "random_password" "database" {
  for_each = local.databases_by_key

  length  = 32
  special = false
}

# Key 'postgresql-password' is the Dagster and Superset Helm charts' own
# convention; every database uses it so consumers stay interchangeable.
resource "kubernetes_secret_v1" "database_credentials" {
  for_each = local.database_secrets

  metadata {
    name      = each.value.name
    namespace = each.value.namespace
    labels    = local.labels
  }
  data = {
    "postgresql-password" = random_password.database[each.value.key].result
  }
  type = "Opaque"
}

resource "kubernetes_secret_v1" "polaris_credentials" {
  metadata {
    name      = var.polaris_credentials_secret_name
    namespace = var.namespace
    labels    = local.labels
  }

  data = {
    username = var.polaris_db_user
    password = random_password.polaris.result
    jdbcUrl  = "jdbc:postgresql://${local.host}:${local.port}/${var.polaris_db_name}"
  }

  type = "Opaque"
}
