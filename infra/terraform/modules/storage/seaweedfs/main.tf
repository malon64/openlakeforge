locals {
  labels = {
    "app.kubernetes.io/name"       = "seaweedfs"
    "app.kubernetes.io/managed-by" = "terraform"
    "openlakeforge.io/component"   = "storage"
  }

  s3_service_name     = "${var.release_name}-s3"
  master_service_name = "${var.release_name}-master"
  filer_service_name  = "${var.release_name}-filer-client"
  access_key_id       = "olf${random_id.s3_access_key.hex}"
}

resource "random_id" "s3_access_key" {
  byte_length = 8
}

resource "random_password" "s3_secret_key" {
  length  = 40
  special = false
}

resource "kubernetes_secret_v1" "s3_credentials" {
  metadata {
    name      = var.credentials_secret_name
    namespace = var.namespace
    labels    = local.labels
  }

  data = {
    AWS_ACCESS_KEY_ID     = local.access_key_id
    AWS_SECRET_ACCESS_KEY = random_password.s3_secret_key.result
  }

  type = "Opaque"
}

resource "helm_release" "seaweedfs" {
  name       = var.release_name
  repository = var.chart_repository
  chart      = "seaweedfs"
  version    = var.chart_version
  namespace  = var.namespace

  wait    = true
  timeout = 300

  values = [
    file(var.values_file),
    yamlencode({
      image = {
        tag = var.image_tag
      }
      s3 = {
        credentials = {
          admin = {
            accessKey = local.access_key_id
            secretKey = random_password.s3_secret_key.result
          }
        }
        createBuckets = []
      }
    }),
  ]
}

resource "kubernetes_job_v1" "bucket" {
  for_each = toset(var.bucket_names)

  metadata {
    name      = "seaweedfs-bucket-${each.key}"
    namespace = var.namespace
    labels    = local.labels
  }

  spec {
    backoff_limit = 3

    template {
      metadata {
        labels = merge(local.labels, {
          "openlakeforge.io/job" = "bucket-bootstrap"
        })
      }

      spec {
        restart_policy = "Never"

        container {
          name  = "create-bucket"
          image = "chrislusf/seaweedfs:${var.image_tag}"

          command = ["/bin/sh", "-ec"]
          args = [<<-SCRIPT
            set -eu

            wait_for_service() {
              url="$1"
              attempt=1
              while [ "$attempt" -le 60 ]; do
                if wget -q --spider "$url" >/dev/null 2>&1; then
                  return 0
                fi
                sleep 5
                attempt=$((attempt + 1))
              done
              echo "Service at $url did not become ready" >&2
              exit 1
            }

            wait_for_service "http://${local.master_service_name}:9333/cluster/status"
            wait_for_service "http://${local.filer_service_name}:8888/"

            export WEED_CLUSTER_DEFAULT=sw
            export WEED_CLUSTER_SW_MASTER="${local.master_service_name}:9333"
            export WEED_CLUSTER_SW_FILER="${local.filer_service_name}:8888"

            bucket_list="$(echo 's3.bucket.list' | /usr/bin/weed shell)"
            if echo "$bucket_list" | awk '{print $1}' | grep -Fxq "${each.key}"; then
              echo "Bucket '${each.key}' already exists."
            else
              echo "Creating bucket '${each.key}'."
              echo 's3.bucket.create --name ${each.key}' | /usr/bin/weed shell
            fi
          SCRIPT
          ]
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
    helm_release.seaweedfs,
  ]
}
