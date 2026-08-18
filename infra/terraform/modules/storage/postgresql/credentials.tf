resource "random_password" "postgres_admin" {
  length  = 32
  special = false
}

resource "random_password" "dagster" {
  length  = 32
  special = false
}

resource "random_password" "openmetadata" {
  count = var.enable_openmetadata ? 1 : 0

  length  = 32
  special = false
}

resource "random_password" "superset" {
  count = var.enable_superset ? 1 : 0

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

# Dagster Helm chart requires a secret with key 'postgresql-password'
resource "kubernetes_secret_v1" "dagster_credentials" {
  metadata {
    name      = var.dagster_credentials_secret_name
    namespace = var.namespace
    labels    = local.labels
  }
  data = {
    "postgresql-password" = random_password.dagster.result
  }
  type = "Opaque"
}

resource "kubernetes_secret_v1" "openmetadata_credentials" {
  count = var.enable_openmetadata ? 1 : 0

  metadata {
    name      = var.openmetadata_credentials_secret_name
    namespace = var.namespace
    labels    = local.labels
  }
  data = {
    "postgresql-password" = random_password.openmetadata[0].result
  }
  type = "Opaque"
}

resource "kubernetes_secret_v1" "superset_credentials" {
  count = var.enable_superset ? 1 : 0

  metadata {
    name      = var.superset_credentials_secret_name
    namespace = var.namespace
    labels    = local.labels
  }
  data = {
    "postgresql-password" = random_password.superset[0].result
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

moved {
  from = random_password.openmetadata
  to   = random_password.openmetadata[0]
}

moved {
  from = random_password.superset
  to   = random_password.superset[0]
}

moved {
  from = kubernetes_secret_v1.openmetadata_credentials
  to   = kubernetes_secret_v1.openmetadata_credentials[0]
}

moved {
  from = kubernetes_secret_v1.superset_credentials
  to   = kubernetes_secret_v1.superset_credentials[0]
}
