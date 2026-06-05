locals {
  labels = {
    "app.kubernetes.io/name"       = "postgresql"
    "app.kubernetes.io/managed-by" = "terraform"
    "openlakeforge.io/component"   = "storage"
  }

  pod_labels = {
    "app.kubernetes.io/name"     = "postgresql"
    "app.kubernetes.io/instance" = var.release_name
  }

  host = "${var.release_name}.${var.namespace}.svc.cluster.local"
  port = 5432
}

resource "random_password" "postgres_admin" {
  length  = 32
  special = false
}

resource "random_password" "dagster" {
  length  = 32
  special = false
}

resource "random_password" "openmetadata" {
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
  metadata {
    name      = var.openmetadata_credentials_secret_name
    namespace = var.namespace
    labels    = local.labels
  }
  data = {
    "postgresql-password" = random_password.openmetadata.result
  }
  type = "Opaque"
}

# Init script runs on first start and creates the additional users + databases.
# Uses postgres:16-alpine's /docker-entrypoint-initdb.d/ hook (bash files are executed
# as the postgres superuser before the server becomes available to clients).
resource "kubernetes_config_map_v1" "init_scripts" {
  metadata {
    name      = "${var.release_name}-init"
    namespace = var.namespace
    labels    = local.labels
  }

  data = {
    "init.sh" = <<-INIT
      #!/bin/bash
      set -e
      psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "postgres" \
        -c "CREATE USER $DAGSTER_DB_USER WITH PASSWORD '$DAGSTER_DB_PASSWORD';" \
        -c "CREATE DATABASE $DAGSTER_DB_NAME OWNER $DAGSTER_DB_USER;" \
        -c "GRANT ALL PRIVILEGES ON DATABASE $DAGSTER_DB_NAME TO $DAGSTER_DB_USER;" \
        -c "CREATE USER $OM_DB_USER WITH PASSWORD '$OM_DB_PASSWORD';" \
        -c "CREATE DATABASE $OM_DB_NAME OWNER $OM_DB_USER;" \
        -c "GRANT ALL PRIVILEGES ON DATABASE $OM_DB_NAME TO $OM_DB_USER;"
    INIT
  }
}

resource "kubernetes_stateful_set_v1" "postgresql" {
  metadata {
    name      = var.release_name
    namespace = var.namespace
    labels    = local.labels
  }

  spec {
    selector {
      match_labels = local.pod_labels
    }
    service_name = var.release_name
    replicas     = 1

    template {
      metadata {
        labels = local.pod_labels
      }

      spec {
        container {
          name  = "postgresql"
          image = "postgres:16-alpine"

          port {
            container_port = 5432
            name           = "postgresql"
          }

          env {
            name  = "POSTGRES_USER"
            value = "postgres"
          }
          env {
            name = "POSTGRES_PASSWORD"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.admin_credentials.metadata[0].name
                key  = "postgres-password"
              }
            }
          }
          env {
            name  = "PGDATA"
            value = "/var/lib/postgresql/data/pgdata"
          }
          env {
            name  = "DAGSTER_DB_USER"
            value = var.dagster_db_user
          }
          env {
            name = "DAGSTER_DB_PASSWORD"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.dagster_credentials.metadata[0].name
                key  = "postgresql-password"
              }
            }
          }
          env {
            name  = "DAGSTER_DB_NAME"
            value = var.dagster_db_name
          }
          env {
            name  = "OM_DB_USER"
            value = var.openmetadata_db_user
          }
          env {
            name = "OM_DB_PASSWORD"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.openmetadata_credentials.metadata[0].name
                key  = "postgresql-password"
              }
            }
          }
          env {
            name  = "OM_DB_NAME"
            value = var.openmetadata_db_name
          }

          volume_mount {
            name       = "data"
            mount_path = "/var/lib/postgresql/data"
          }

          volume_mount {
            name       = "init-scripts"
            mount_path = "/docker-entrypoint-initdb.d"
          }

          readiness_probe {
            exec {
              command = ["pg_isready", "-U", "postgres"]
            }
            initial_delay_seconds = 5
            period_seconds        = 10
            failure_threshold     = 6
          }

          liveness_probe {
            exec {
              command = ["pg_isready", "-U", "postgres"]
            }
            initial_delay_seconds = 30
            period_seconds        = 30
          }

          resources {
            requests = {
              cpu    = "100m"
              memory = "256Mi"
            }
            limits = {
              memory = "512Mi"
            }
          }
        }

        volume {
          name = "init-scripts"
          config_map {
            name         = kubernetes_config_map_v1.init_scripts.metadata[0].name
            default_mode = "0755"
          }
        }
      }
    }

    volume_claim_template {
      metadata {
        name   = "data"
        labels = local.labels
      }

      spec {
        access_modes       = ["ReadWriteOnce"]
        storage_class_name = var.storage_class_name

        resources {
          requests = {
            storage = var.storage_size
          }
        }
      }
    }
  }

  wait_for_rollout = true
}

resource "kubernetes_service_v1" "postgresql" {
  metadata {
    name      = var.release_name
    namespace = var.namespace
    labels    = local.labels
  }

  spec {
    selector = local.pod_labels

    port {
      port        = 5432
      target_port = "5432"
      name        = "postgresql"
    }

    type = "ClusterIP"
  }
}
