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

resource "kubernetes_persistent_volume_claim_v1" "reports" {
  wait_until_bound = false

  metadata {
    name      = local.reports_claim_name
    namespace = var.namespace
    labels    = local.labels
  }

  spec {
    access_modes       = ["ReadWriteOnce"]
    storage_class_name = var.reports_storage_class_name

    resources {
      requests = {
        storage = var.reports_storage_size
      }
    }
  }
}

resource "helm_release" "superset" {
  name       = var.release_name
  repository = var.chart_repository
  chart      = "superset"
  version    = var.chart_version
  namespace  = var.namespace

  # wait = true causes the provider to pass --wait to both helm install and
  # helm uninstall. On destroy this means Helm waits for all pods to fully
  # terminate before returning. Once pods are gone Kubernetes removes the
  # pvc-protection finalizer automatically, so the PVC deletion below
  # succeeds without any additional workarounds.
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

      extraVolumes = [
        {
          name = "superset-reports"
          persistentVolumeClaim = {
            claimName = kubernetes_persistent_volume_claim_v1.reports.metadata[0].name
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
