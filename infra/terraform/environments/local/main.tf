terraform {
  required_version = ">= 1.6.0"

  required_providers {
    helm = {
      source  = "hashicorp/helm"
      version = "~> 3.1"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.36"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.7"
    }
  }
}

provider "kubernetes" {
  config_path    = local.kubeconfig_path
  config_context = var.kube_context
}

provider "helm" {
  kubernetes = {
    config_path    = local.kubeconfig_path
    config_context = var.kube_context
  }
}

locals {
  kubeconfig_path            = var.kubeconfig_path != null ? pathexpand(var.kubeconfig_path) : pathexpand("~/.kube/config")
  sales_floe_artifact_prefix = "floe/sales"
  sales_floe_manifest_key    = "${local.sales_floe_artifact_prefix}/sales.manifest.json"
  sales_floe_config_key      = "${local.sales_floe_artifact_prefix}/sales_poc.yml"
  sales_floe_manifest_uri    = "s3://${var.code_bucket_name}/${local.sales_floe_manifest_key}"
  polaris_bootstrap_hash     = filesha256("${path.root}/../../modules/catalog/polaris/main.tf")
  sales_floe_artifact_hash = substr(sha256(join("\n", [
    file("${path.root}/../../../../domains/sales/contracts/floe/manifests/sales.manifest.json"),
    file("${path.root}/../../../../domains/sales/contracts/floe/sales_poc.yml"),
    module.polaris.contract.bootstrap_run_id,
    local.polaris_bootstrap_hash,
  ])), 0, 12)
}

resource "kubernetes_namespace_v1" "lakehouse" {
  metadata {
    name = var.namespace
  }
}

module "seaweedfs" {
  source = "../../modules/storage/seaweedfs"

  namespace        = kubernetes_namespace_v1.lakehouse.metadata[0].name
  base_values_file = "${path.root}/../../../helm/values/local/seaweedfs.yaml"
  bucket_names = [
    var.iceberg_bucket_name,
    var.code_bucket_name,
  ]
  region = var.s3_region
}

resource "kubernetes_config_map_v1" "sales_floe_artifacts" {
  metadata {
    name      = "sales-floe-artifacts-${local.sales_floe_artifact_hash}"
    namespace = kubernetes_namespace_v1.lakehouse.metadata[0].name
    labels = {
      "app.kubernetes.io/name"       = "sales-floe-artifacts"
      "app.kubernetes.io/managed-by" = "terraform"
      "openlakeforge.io/component"   = "orchestration-artifact"
    }
  }

  data = {
    "sales.manifest.json" = file("${path.root}/../../../../domains/sales/contracts/floe/manifests/sales.manifest.json")
    "sales_poc.yml"       = file("${path.root}/../../../../domains/sales/contracts/floe/sales_poc.yml")
  }
}

resource "kubernetes_job_v1" "sales_floe_artifact_upload" {
  metadata {
    name      = "sales-floe-artifact-upload-${local.sales_floe_artifact_hash}"
    namespace = kubernetes_namespace_v1.lakehouse.metadata[0].name
    labels = {
      "app.kubernetes.io/name"       = "sales-floe-artifact-upload"
      "app.kubernetes.io/managed-by" = "terraform"
      "openlakeforge.io/component"   = "orchestration-artifact"
    }
  }

  spec {
    backoff_limit = 3

    template {
      metadata {
        labels = {
          "app.kubernetes.io/name"     = "sales-floe-artifact-upload"
          "openlakeforge.io/component" = "orchestration-artifact"
        }
      }

      spec {
        restart_policy = "Never"

        container {
          name  = "upload"
          image = "amazon/aws-cli:2.17.63"

          command = ["/bin/sh", "-ec"]
          args = [<<-SCRIPT
            set -eu

            export AWS_EC2_METADATA_DISABLED=true
            export AWS_CONFIG_FILE=/tmp/aws/config
            mkdir -p /tmp/aws
            aws configure set default.s3.addressing_style path

            for attempt in $(seq 1 60); do
              if aws --endpoint-url "${module.seaweedfs.contract.endpoint}" s3 ls "s3://${var.code_bucket_name}" >/dev/null 2>&1; then
                break
              fi
              if [ "$attempt" = "60" ]; then
                echo "Bucket '${var.code_bucket_name}' did not become available." >&2
                exit 1
              fi
              sleep 5
            done

            escape_sed_replacement() {
              printf '%s' "$1" | sed 's/[&|\\]/\\&/g'
            }

            floe_client_id="$(escape_sed_replacement "$POLARIS_FLOE_CLIENT_ID")"
            floe_client_secret="$(escape_sed_replacement "$POLARIS_FLOE_CLIENT_SECRET")"

            sed \
              -e 's|$${POLARIS_FLOE_CLIENT_ID}|'"$floe_client_id"'|g' \
              -e 's|$${POLARIS_FLOE_CLIENT_SECRET}|'"$floe_client_secret"'|g' \
              /artifacts/sales.manifest.json > /tmp/sales.manifest.json

            aws --endpoint-url "${module.seaweedfs.contract.endpoint}" s3 cp \
              /tmp/sales.manifest.json \
              "s3://${var.code_bucket_name}/${local.sales_floe_manifest_key}" \
              --content-type application/json

            aws --endpoint-url "${module.seaweedfs.contract.endpoint}" s3 cp \
              /artifacts/sales_poc.yml \
              "s3://${var.code_bucket_name}/${local.sales_floe_config_key}" \
              --content-type application/yaml
          SCRIPT
          ]

          env {
            name  = "AWS_REGION"
            value = module.seaweedfs.contract.region
          }

          env {
            name  = "AWS_DEFAULT_REGION"
            value = module.seaweedfs.contract.region
          }

          env {
            name  = "AWS_S3_FORCE_PATH_STYLE"
            value = tostring(module.seaweedfs.contract.path_style_access)
          }

          env {
            name = "AWS_ACCESS_KEY_ID"
            value_from {
              secret_key_ref {
                name = module.seaweedfs.contract.credentials_secret_name
                key  = module.seaweedfs.contract.access_key_id_key
              }
            }
          }

          env {
            name = "AWS_SECRET_ACCESS_KEY"
            value_from {
              secret_key_ref {
                name = module.seaweedfs.contract.credentials_secret_name
                key  = module.seaweedfs.contract.secret_access_key_key
              }
            }
          }

          env {
            name = "POLARIS_FLOE_CLIENT_ID"
            value_from {
              secret_key_ref {
                name = module.polaris.contract.floe_credentials_secret_name
                key  = module.polaris.contract.floe_client_id_key
              }
            }
          }

          env {
            name = "POLARIS_FLOE_CLIENT_SECRET"
            value_from {
              secret_key_ref {
                name = module.polaris.contract.floe_credentials_secret_name
                key  = module.polaris.contract.floe_client_secret_key
              }
            }
          }

          volume_mount {
            name       = "artifacts"
            mount_path = "/artifacts"
            read_only  = true
          }
        }

        volume {
          name = "artifacts"
          config_map {
            name = kubernetes_config_map_v1.sales_floe_artifacts.metadata[0].name
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
    module.seaweedfs,
    module.polaris,
  ]
}

module "polaris" {
  source = "../../modules/catalog/polaris"

  namespace        = kubernetes_namespace_v1.lakehouse.metadata[0].name
  base_values_file = "${path.root}/../../../helm/values/local/polaris.yaml"
  catalog_name     = var.catalog_name
  principal_name   = "trino"
  principal_role   = "data-engineer"
  catalog_role     = "catalog-admin"
  storage_contract = module.seaweedfs.contract

  depends_on = [
    module.seaweedfs,
  ]
}

module "trino" {
  source = "../../modules/query/trino"

  namespace                  = kubernetes_namespace_v1.lakehouse.metadata[0].name
  base_values_file           = "${path.root}/../../../helm/values/local/trino.yaml"
  storage_contract           = module.seaweedfs.contract
  catalog_contract           = module.polaris.contract
  catalog_bootstrap_revision = local.polaris_bootstrap_hash

  depends_on = [
    module.polaris,
  ]
}

module "dagster" {
  source = "../../modules/orchestration/dagster"

  namespace                      = kubernetes_namespace_v1.lakehouse.metadata[0].name
  base_values_file               = "${path.root}/../../../helm/values/local/dagster.yaml"
  project_code_image_repository  = var.project_code_image_repository
  project_code_image_tag         = var.project_code_image_tag
  project_code_image_pull_policy = var.project_code_image_pull_policy
  project_code_image_revision    = var.project_code_image_revision
  storage_contract               = module.seaweedfs.contract
  catalog_contract               = module.polaris.contract
  floe_manifest_uri              = local.sales_floe_manifest_uri
  floe_manifest_revision         = local.sales_floe_artifact_hash

  depends_on = [
    module.trino,
    kubernetes_job_v1.sales_floe_artifact_upload,
  ]
}
