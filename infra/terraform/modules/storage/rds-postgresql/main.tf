locals {
  labels = {
    "app.kubernetes.io/name"       = "rds-postgresql"
    "app.kubernetes.io/managed-by" = "terraform"
    "openlakeforge.io/component"   = "metadata-database"
  }

  host = aws_db_instance.this.address
  port = aws_db_instance.this.port
}

resource "random_password" "master" {
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

resource "random_password" "superset" {
  length  = 32
  special = false
}

resource "aws_security_group" "this" {
  name_prefix = "${var.name_prefix}-rds-"
  description = "OpenLakeForge AWS POC RDS PostgreSQL access"
  vpc_id      = var.vpc_id

  ingress {
    description = "PostgreSQL from EKS VPC CIDRs"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = var.allowed_cidr_blocks
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Project     = "openlakeforge"
    Environment = "aws-poc"
  }
}

resource "aws_db_subnet_group" "this" {
  name       = "${var.name_prefix}-rds"
  subnet_ids = var.subnet_ids

  tags = {
    Project     = "openlakeforge"
    Environment = "aws-poc"
  }
}

resource "aws_db_instance" "this" {
  identifier             = "${var.name_prefix}-metadata"
  engine                 = "postgres"
  engine_version         = var.engine_version
  instance_class         = var.instance_class
  allocated_storage      = var.allocated_storage
  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.this.id]
  username               = var.master_username
  password               = random_password.master.result
  db_name                = "postgres"
  publicly_accessible    = false
  skip_final_snapshot    = true
  deletion_protection    = false
  apply_immediately      = true
  storage_encrypted      = true

  tags = {
    Project     = "openlakeforge"
    Environment = "aws-poc"
  }
}

resource "kubernetes_secret_v1" "dagster" {
  metadata {
    name      = var.dagster_credentials_secret_name
    namespace = var.namespace
    labels    = local.labels
  }

  data = {
    postgresql-password = random_password.dagster.result
  }
}

resource "kubernetes_secret_v1" "openmetadata" {
  metadata {
    name      = var.openmetadata_credentials_secret_name
    namespace = var.namespace
    labels    = local.labels
  }

  data = {
    postgresql-password = random_password.openmetadata.result
  }
}

resource "kubernetes_secret_v1" "superset" {
  metadata {
    name      = var.superset_credentials_secret_name
    namespace = var.namespace
    labels    = local.labels
  }

  data = {
    postgresql-password = random_password.superset.result
  }
}

resource "kubernetes_secret_v1" "master" {
  metadata {
    name      = "${var.name_prefix}-rds-master"
    namespace = var.namespace
    labels    = local.labels
  }

  data = {
    username = var.master_username
    password = random_password.master.result
  }
}

resource "kubernetes_job_v1" "bootstrap" {
  metadata {
    name      = "${var.name_prefix}-rds-bootstrap"
    namespace = var.namespace
    labels    = local.labels
  }

  spec {
    backoff_limit = 6

    template {
      metadata {
        labels = merge(local.labels, {
          "openlakeforge.io/job" = "rds-bootstrap"
        })
      }

      spec {
        restart_policy = "Never"

        container {
          name  = "bootstrap"
          image = "postgres:16-alpine"

          command = ["/bin/sh", "-ec"]
          args = [<<-SCRIPT
            set -eu

            until PGPASSWORD="$MASTER_PASSWORD" psql \
              --host="${local.host}" \
              --port="${local.port}" \
              --username="$MASTER_USERNAME" \
              --dbname=postgres \
              -c "select 1" >/dev/null 2>&1; do
              sleep 5
            done

            create_role_and_db() {
              db_name="$1"
              db_user="$2"
              db_password="$3"

              PGPASSWORD="$MASTER_PASSWORD" psql \
                --host="${local.host}" \
                --port="${local.port}" \
                --username="$MASTER_USERNAME" \
                --dbname=postgres \
                --set=db_name="$db_name" \
                --set=db_user="$db_user" \
                --set=db_password="$db_password" <<'SQL'
            SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'db_user', :'db_password')
            WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'db_user')\gexec
            SELECT format('ALTER ROLE %I WITH LOGIN PASSWORD %L', :'db_user', :'db_password')\gexec
            SELECT format('CREATE DATABASE %I OWNER %I', :'db_name', :'db_user')
            WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'db_name')\gexec
            SQL
            }

            create_role_and_db "${var.dagster_db_name}" "${var.dagster_db_user}" "$DAGSTER_PASSWORD"
            create_role_and_db "${var.openmetadata_db_name}" "${var.openmetadata_db_user}" "$OPENMETADATA_PASSWORD"
            create_role_and_db "${var.superset_db_name}" "${var.superset_db_user}" "$SUPERSET_PASSWORD"
          SCRIPT
          ]

          env {
            name  = "PGSSLMODE"
            value = "require"
          }

          env {
            name = "MASTER_USERNAME"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.master.metadata[0].name
                key  = "username"
              }
            }
          }

          env {
            name = "MASTER_PASSWORD"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.master.metadata[0].name
                key  = "password"
              }
            }
          }

          env {
            name = "DAGSTER_PASSWORD"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.dagster.metadata[0].name
                key  = "postgresql-password"
              }
            }
          }

          env {
            name = "OPENMETADATA_PASSWORD"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.openmetadata.metadata[0].name
                key  = "postgresql-password"
              }
            }
          }

          env {
            name = "SUPERSET_PASSWORD"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.superset.metadata[0].name
                key  = "postgresql-password"
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
    aws_db_instance.this,
  ]
}
