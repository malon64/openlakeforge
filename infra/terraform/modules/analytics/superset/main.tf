locals {
  labels = {
    "app.kubernetes.io/name"       = "superset"
    "app.kubernetes.io/managed-by" = "terraform"
    "openlakeforge.io/component"   = "analytics"
  }

  reports_claim_name = "${var.release_name}-reports"
}

resource "random_password" "secret_key" {
  length  = 64
  special = false
}

resource "helm_release" "superset" {
  name       = var.release_name
  repository = var.chart_repository
  chart      = "superset"
  version    = var.chart_version
  namespace  = var.namespace

  wait            = true
  timeout         = 900
  cleanup_on_fail = true
  upgrade_install = true

  values = [
    file(var.base_values_file),
    yamlencode({
      image = {
        repository = var.image_repository
        tag        = var.image_tag
        pullPolicy = var.image_pull_policy
      }

      postgresql = {
        enabled = false
      }

      service = {
        type = "ClusterIP"
        port = var.http_port
      }

      supersetNode = {
        connections = {
          db_type = "postgresql"
          db_host = var.postgresql_contract.host
          db_port = tostring(var.postgresql_contract.port)
          db_user = var.postgresql_contract.superset_db_user
          db_pass = "managed-by-${var.postgresql_contract.superset_credentials_secret_name}"
          db_name = var.postgresql_contract.superset_db_name
        }
      }

      init = {
        createAdmin = true
        adminUser = {
          username  = var.admin_username
          firstname = "OpenLakeForge"
          lastname  = "Admin"
          email     = var.admin_email
          password  = var.admin_password
        }
      }

      extraEnvRaw = [
        {
          name = "DB_PASS"
          valueFrom = {
            secretKeyRef = {
              name = var.postgresql_contract.superset_credentials_secret_name
              key  = "postgresql-password"
            }
          }
        },
      ]

      extraSecretEnv = {
        SUPERSET_SECRET_KEY = random_password.secret_key.result
      }

      # The reports PVC is declared here as an extraObject so that Helm owns its
      # full lifecycle. When `helm uninstall` runs, Helm deletes the pods and the
      # PVC together; the pvc-protection finalizer is cleared by Kubernetes once
      # the pods finish terminating, without Terraform ever blocking on it.
      extraObjects = [
        {
          apiVersion = "v1"
          kind       = "PersistentVolumeClaim"
          metadata = {
            name      = local.reports_claim_name
            namespace = var.namespace
            labels    = local.labels
          }
          spec = {
            accessModes      = ["ReadWriteOnce"]
            storageClassName = var.reports_storage_class_name
            resources = {
              requests = {
                storage = var.reports_storage_size
              }
            }
          }
        },
      ]

      extraVolumes = [
        {
          name = "superset-reports"
          persistentVolumeClaim = {
            claimName = local.reports_claim_name
          }
        },
      ]

      extraVolumeMounts = [
        {
          name      = "superset-reports"
          mountPath = var.reports_mount_path
        },
      ]
    }),
  ]
}
