locals {
  labels = {
    "app.kubernetes.io/name"       = "rds-postgresql"
    "app.kubernetes.io/managed-by" = "terraform"
    "openlakeforge.io/component"   = "metadata-database"
  }

  host = aws_db_instance.this.address
  port = aws_db_instance.this.port

  databases_by_key = { for database in var.databases : database.key => database }

  # One env-var prefix per database, so the bootstrap script can address each
  # without the module knowing which services exist.
  database_env_prefixes = { for database in var.databases : database.key => "OLF_DB_${upper(replace(database.key, "-", "_"))}" }

  # The credentials Secret always exists in this module's own namespace - the
  # bootstrap Job mounts it - plus wherever the consuming workload runs, since
  # a Secret cannot be read across namespaces.
  database_secrets = merge(concat([{}], [
    for database in var.databases : {
      for target in toset(concat([var.namespace], database.namespaces)) :
      "${database.key}/${target}" => {
        key       = database.key
        namespace = target
        name      = database.credentials_secret_name
      }
    }
  ])...)

  bootstrap_script = templatefile("${path.module}/templates/init.sh.tftpl", {
    database_env_prefixes = [for database in var.databases : local.database_env_prefixes[database.key]]
  })

  bootstrap_hash = substr(sha256(jsonencode({
    script    = local.bootstrap_script
    databases = var.databases
  })), 0, 12)
}

resource "random_password" "master" {
  length  = 32
  special = false
}

resource "random_password" "database" {
  for_each = local.databases_by_key

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
    name      = "${var.name_prefix}-rds-bootstrap-${local.bootstrap_hash}"
    namespace = var.namespace
    labels    = local.labels
  }

  spec {
    backoff_limit = 6

    template {
      metadata {
        labels = merge(local.labels, {
          "openlakeforge.io/job"       = "rds-bootstrap"
          "openlakeforge.io/readiness" = "required"
        })
      }

      spec {
        restart_policy = "Never"

        container {
          name  = "bootstrap"
          image = "postgres:16-alpine@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777"

          command = ["/bin/sh", "-ec"]
          args    = [local.bootstrap_script]

          env {
            name  = "PGSSLMODE"
            value = "require"
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
            name = "POSTGRES_USER"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.master.metadata[0].name
                key  = "username"
              }
            }
          }

          env {
            name = "PGPASSWORD"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.master.metadata[0].name
                key  = "password"
              }
            }
          }

          dynamic "env" {
            for_each = local.databases_by_key
            content {
              name  = "${local.database_env_prefixes[env.key]}_USER"
              value = env.value.db_user
            }
          }

          dynamic "env" {
            for_each = local.databases_by_key
            content {
              name  = "${local.database_env_prefixes[env.key]}_NAME"
              value = env.value.db_name
            }
          }

          dynamic "env" {
            for_each = local.databases_by_key
            content {
              name = "${local.database_env_prefixes[env.key]}_PASSWORD"
              value_from {
                secret_key_ref {
                  name = kubernetes_secret_v1.database_credentials["${env.key}/${var.namespace}"].metadata[0].name
                  key  = "postgresql-password"
                }
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
