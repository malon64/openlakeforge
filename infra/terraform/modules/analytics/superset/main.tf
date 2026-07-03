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
    access_modes       = var.reports_access_modes
    storage_class_name = var.reports_storage_class_name

    resources {
      requests = {
        storage = var.reports_storage_size
      }
    }
  }
}

# Destroy-time teardown guard.
#
# In a clean `terraform destroy`, helm_release.superset (wait = true) removes the
# Superset workloads before the reports PVC is deleted, so the RWO PVC's
# kubernetes.io/pvc-protection finalizer clears on its own. But when a prior
# destroy is interrupted (e.g. SSO token expiry mid-run) the release secret can
# be gone while the Deployments are orphaned; those orphaned pods keep the PVC
# mounted and it hangs in Terminating indefinitely, failing `make aws-down`.
#
# This resource runs a best-effort cleanup at destroy time BEFORE the PVC is
# deleted: it force-removes any lingering Superset workloads (which drops the
# pods mounting the PVC) and, as a backstop, strips the PVC finalizer if it is
# still stuck. Everything is idempotent and `|| true` guarded so a healthy
# teardown is a no-op and a partially torn-down one still converges.
resource "terraform_data" "reports_teardown_guard" {
  # depends_on (not a reference) so this guard is destroyed BEFORE the PVC and
  # the helm release, letting its cleanup run ahead of the PVC deletion.
  depends_on = [
    kubernetes_persistent_volume_claim_v1.reports,
    helm_release.superset,
  ]

  # Destroy provisioners may only read `self`, so carry everything in `input`.
  input = {
    kubeconfig = var.kubeconfig_path
    context    = var.kube_context
    namespace  = var.namespace
    release    = var.release_name
    claim      = local.reports_claim_name
  }

  provisioner "local-exec" {
    when        = destroy
    interpreter = ["/bin/bash", "-c"]
    command     = <<-EOT
      set -u
      export KUBECONFIG="${self.input.kubeconfig}"
      CTX="${self.input.context}"
      NS="${self.input.namespace}"
      REL="${self.input.release}"
      PVC="${self.input.claim}"

      if ! kubectl --context "$CTX" -n "$NS" get ns "$NS" >/dev/null 2>&1; then
        echo "[superset teardown guard] namespace $NS gone; nothing to clean"
        exit 0
      fi

      echo "[superset teardown guard] force-removing lingering Superset workloads"
      kubectl --context "$CTX" -n "$NS" delete deploy,statefulset,replicaset,job \
        -l "app.kubernetes.io/instance=$REL" --ignore-not-found --wait=false || true

      # If the reports PVC is still stuck Terminating, clear the finalizer so the
      # Terraform PVC delete can complete.
      if kubectl --context "$CTX" -n "$NS" get pvc "$PVC" >/dev/null 2>&1; then
        echo "[superset teardown guard] clearing pvc-protection finalizer on $PVC"
        kubectl --context "$CTX" -n "$NS" patch pvc "$PVC" \
          --type=merge -p '{"metadata":{"finalizers":null}}' || true
      fi
    EOT
  }
}

resource "helm_release" "superset" {
  name       = var.release_name
  repository = var.chart_repository
  chart      = "superset"
  version    = var.chart_version
  namespace  = var.namespace

  # wait = true causes the provider to pass --wait to both helm install and
  # helm uninstall. The init hook also deletes itself after success so its
  # completed pod does not keep the reports PVC under pvc-protection.
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
          db_type  = "postgresql"
          db_host  = var.postgresql_contract.host
          db_port  = tostring(var.postgresql_contract.port)
          db_user  = var.postgresql_contract.superset_db_user
          db_pass  = "managed-by-${var.postgresql_contract.superset_credentials_secret_name}"
          db_name  = var.postgresql_contract.superset_db_name
          db_extra = var.postgresql_ssl_mode == "disable" ? "" : "?sslmode=${var.postgresql_ssl_mode}"
        }
      }

      init = {
        createAdmin = true
        jobAnnotations = {
          "helm.sh/hook"               = "post-install,post-upgrade"
          "helm.sh/hook-delete-policy" = "before-hook-creation,hook-succeeded"
        }
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
