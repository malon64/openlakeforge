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

  bootstrap_script = <<-SCRIPT
    #!/bin/sh
    set -eu

    psql_with_retry() {
      attempt=1
      while true; do
        psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" "$@" && return 0
        status=$?
        if [ "$status" -ne 2 ] || [ "$attempt" -ge 30 ]; then
          return "$status"
        fi
        echo "PostgreSQL connection was lost; retrying bootstrap query ($attempt/30)..." >&2
        attempt=$((attempt + 1))
        sleep 2
      done
    }

    psql_base() {
      psql_with_retry --dbname postgres "$@"
    }

    create_or_update_role() {
      role_name="$1"
      role_password="$2"

      psql_base -c "DO \$\$
    BEGIN
      IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '$role_name') THEN
        CREATE ROLE \"$role_name\" LOGIN PASSWORD '$role_password';
      ELSE
        ALTER ROLE \"$role_name\" WITH LOGIN PASSWORD '$role_password';
      END IF;
    END
    \$\$;"
    }

    create_database_if_missing() {
      db_name="$1"
      owner_name="$2"

      if psql_base -tAc "SELECT 1 FROM pg_database WHERE datname = '$db_name'" | grep -qx 1; then
        echo "Database '$db_name' already exists."
      else
        psql_base -c "CREATE DATABASE \"$db_name\" OWNER \"$owner_name\";"
      fi

      psql_base -c "ALTER DATABASE \"$db_name\" OWNER TO \"$owner_name\";"
      psql_base -c "GRANT ALL PRIVILEGES ON DATABASE \"$db_name\" TO \"$owner_name\";"
      psql_with_retry --dbname "$db_name" \
        -c "GRANT ALL ON SCHEMA public TO \"$owner_name\";" \
        -c "ALTER SCHEMA public OWNER TO \"$owner_name\";"
    }

    create_or_update_role "$DAGSTER_DB_USER" "$DAGSTER_DB_PASSWORD"
    create_database_if_missing "$DAGSTER_DB_NAME" "$DAGSTER_DB_USER"

    ${var.enable_openmetadata ? <<-SCRIPT
    create_or_update_role "$OM_DB_USER" "$OM_DB_PASSWORD"
    create_database_if_missing "$OM_DB_NAME" "$OM_DB_USER"
    SCRIPT
  : ""}

    ${var.enable_superset ? <<-SCRIPT
    create_or_update_role "$SUPERSET_DB_USER" "$SUPERSET_DB_PASSWORD"
    create_database_if_missing "$SUPERSET_DB_NAME" "$SUPERSET_DB_USER"
    SCRIPT
: ""}

    create_or_update_role "$POLARIS_DB_USER" "$POLARIS_DB_PASSWORD"
    create_database_if_missing "$POLARIS_DB_NAME" "$POLARIS_DB_USER"
  SCRIPT

bootstrap_hash = substr(sha256(jsonencode({
  script               = local.bootstrap_script
  dagster_db_name      = var.dagster_db_name
  dagster_db_user      = var.dagster_db_user
  openmetadata_db_name = var.openmetadata_db_name
  openmetadata_db_user = var.openmetadata_db_user
  enable_openmetadata  = var.enable_openmetadata
  superset_db_name     = var.superset_db_name
  superset_db_user     = var.superset_db_user
  enable_superset      = var.enable_superset
  polaris_db_name      = var.polaris_db_name
  polaris_db_user      = var.polaris_db_user
})), 0, 12)
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

# Init script runs on first start and creates the additional users + databases.
# Uses postgres:16-alpine's /docker-entrypoint-initdb.d/ hook (shell files are executed
# as the postgres superuser before the server becomes available to clients).
resource "kubernetes_config_map_v1" "init_scripts" {
  metadata {
    name      = "${var.release_name}-init"
    namespace = var.namespace
    labels    = local.labels
  }

  data = {
    "init.sh" = local.bootstrap_script
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
          image = "postgres:16-alpine@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777"

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
          dynamic "env" {
            for_each = var.enable_openmetadata ? [true] : []
            content {
              name  = "OM_DB_USER"
              value = var.openmetadata_db_user
            }
          }
          dynamic "env" {
            for_each = var.enable_openmetadata ? [true] : []
            content {
              name = "OM_DB_PASSWORD"
              value_from {
                secret_key_ref {
                  name = kubernetes_secret_v1.openmetadata_credentials[0].metadata[0].name
                  key  = "postgresql-password"
                }
              }
            }
          }
          dynamic "env" {
            for_each = var.enable_openmetadata ? [true] : []
            content {
              name  = "OM_DB_NAME"
              value = var.openmetadata_db_name
            }
          }
          dynamic "env" {
            for_each = var.enable_superset ? [true] : []
            content {
              name  = "SUPERSET_DB_USER"
              value = var.superset_db_user
            }
          }
          dynamic "env" {
            for_each = var.enable_superset ? [true] : []
            content {
              name = "SUPERSET_DB_PASSWORD"
              value_from {
                secret_key_ref {
                  name = kubernetes_secret_v1.superset_credentials[0].metadata[0].name
                  key  = "postgresql-password"
                }
              }
            }
          }
          dynamic "env" {
            for_each = var.enable_superset ? [true] : []
            content {
              name  = "SUPERSET_DB_NAME"
              value = var.superset_db_name
            }
          }
          env {
            name  = "POLARIS_DB_USER"
            value = var.polaris_db_user
          }
          env {
            name = "POLARIS_DB_PASSWORD"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.polaris_credentials.metadata[0].name
                key  = "password"
              }
            }
          }
          env {
            name  = "POLARIS_DB_NAME"
            value = var.polaris_db_name
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

resource "kubernetes_job_v1" "bootstrap" {
  metadata {
    name      = "${var.release_name}-bootstrap-${local.bootstrap_hash}"
    namespace = var.namespace
    labels    = local.labels
  }

  spec {
    backoff_limit = 3

    template {
      metadata {
        labels = merge(local.pod_labels, {
          "openlakeforge.io/job"       = "postgresql-bootstrap"
          "openlakeforge.io/readiness" = "required"
        })
      }

      spec {
        restart_policy = "Never"

        container {
          name  = "bootstrap"
          image = "postgres:16-alpine@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777"

          command = ["/bin/sh", "-ec"]
          args = [<<-SCRIPT
            until pg_isready -h "${local.host}" -p "${local.port}" -U "$POSTGRES_USER"; do
              sleep 2
            done

            /bootstrap/init.sh
          SCRIPT
          ]

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
            name = "PGPASSWORD"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.admin_credentials.metadata[0].name
                key  = "postgres-password"
              }
            }
          }
          env {
            name  = "PGHOST"
            value = local.host
          }
          env {
            name  = "PGPORT"
            value = tostring(local.port)
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
          dynamic "env" {
            for_each = var.enable_openmetadata ? [true] : []
            content {
              name  = "OM_DB_USER"
              value = var.openmetadata_db_user
            }
          }
          dynamic "env" {
            for_each = var.enable_openmetadata ? [true] : []
            content {
              name = "OM_DB_PASSWORD"
              value_from {
                secret_key_ref {
                  name = kubernetes_secret_v1.openmetadata_credentials[0].metadata[0].name
                  key  = "postgresql-password"
                }
              }
            }
          }
          dynamic "env" {
            for_each = var.enable_openmetadata ? [true] : []
            content {
              name  = "OM_DB_NAME"
              value = var.openmetadata_db_name
            }
          }
          dynamic "env" {
            for_each = var.enable_superset ? [true] : []
            content {
              name  = "SUPERSET_DB_USER"
              value = var.superset_db_user
            }
          }
          dynamic "env" {
            for_each = var.enable_superset ? [true] : []
            content {
              name = "SUPERSET_DB_PASSWORD"
              value_from {
                secret_key_ref {
                  name = kubernetes_secret_v1.superset_credentials[0].metadata[0].name
                  key  = "postgresql-password"
                }
              }
            }
          }
          dynamic "env" {
            for_each = var.enable_superset ? [true] : []
            content {
              name  = "SUPERSET_DB_NAME"
              value = var.superset_db_name
            }
          }
          env {
            name  = "POLARIS_DB_USER"
            value = var.polaris_db_user
          }
          env {
            name = "POLARIS_DB_PASSWORD"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.polaris_credentials.metadata[0].name
                key  = "password"
              }
            }
          }
          env {
            name  = "POLARIS_DB_NAME"
            value = var.polaris_db_name
          }

          volume_mount {
            name       = "init-scripts"
            mount_path = "/bootstrap"
            read_only  = true
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
  }

  wait_for_completion = true

  timeouts {
    create = "5m"
    update = "5m"
  }

  depends_on = [
    kubernetes_stateful_set_v1.postgresql,
    kubernetes_service_v1.postgresql,
  ]
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
