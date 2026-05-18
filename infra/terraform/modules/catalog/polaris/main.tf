locals {
  labels = {
    "app.kubernetes.io/name"       = "polaris"
    "app.kubernetes.io/managed-by" = "terraform"
    "openlakeforge.io/component"   = "catalog"
  }

  root_client_id = "root"
  realm          = "POLARIS"
  rest_uri       = "http://${var.release_name}:8181/api/catalog"
  token_uri      = "http://${var.release_name}:8181/api/catalog/v1/oauth/tokens"
  oauth_scope    = "PRINCIPAL_ROLE:ALL"
  bootstrap_annotations = {
    "openlakeforge.io/polaris-release-revision" = tostring(helm_release.polaris.metadata.revision)
  }
}

resource "random_password" "root_client_secret" {
  length  = 32
  special = false
}

resource "kubernetes_secret_v1" "bootstrap_credentials" {
  metadata {
    name      = var.bootstrap_secret_name
    namespace = var.namespace
    labels    = local.labels
  }

  data = {
    POLARIS_BOOTSTRAP_CREDENTIALS = "${local.realm},${local.root_client_id},${random_password.root_client_secret.result}"
    ROOT_CLIENT_ID                = local.root_client_id
    ROOT_CLIENT_SECRET            = random_password.root_client_secret.result
  }

  type = "Opaque"
}

resource "helm_release" "polaris" {
  name       = var.release_name
  repository = var.chart_repository
  chart      = "polaris"
  version    = var.chart_version
  namespace  = var.namespace

  wait    = true
  timeout = 300

  values = [
    file(var.base_values_file),
    yamlencode({
      extraEnv = [
        {
          name = "POLARIS_BOOTSTRAP_CREDENTIALS"
          valueFrom = {
            secretKeyRef = {
              name = kubernetes_secret_v1.bootstrap_credentials.metadata[0].name
              key  = "POLARIS_BOOTSTRAP_CREDENTIALS"
            }
          }
        },
        {
          name  = "AWS_REGION"
          value = var.storage_contract.region
        },
        {
          name  = "AWS_ENDPOINT_URL_S3"
          value = var.storage_contract.endpoint
        },
        {
          name  = "AWS_S3_FORCE_PATH_STYLE"
          value = tostring(var.storage_contract.path_style_access)
        },
        {
          name = "AWS_ACCESS_KEY_ID"
          valueFrom = {
            secretKeyRef = {
              name = var.storage_contract.credentials_secret_name
              key  = var.storage_contract.access_key_id_key
            }
          }
        },
        {
          name = "AWS_SECRET_ACCESS_KEY"
          valueFrom = {
            secretKeyRef = {
              name = var.storage_contract.credentials_secret_name
              key  = var.storage_contract.secret_access_key_key
            }
          }
        },
      ]
    }),
  ]
}

resource "terraform_data" "polaris_release_revision" {
  triggers_replace = [
    helm_release.polaris.metadata.revision,
  ]
}

resource "kubernetes_service_account_v1" "bootstrap" {
  metadata {
    name      = "polaris-bootstrap"
    namespace = var.namespace
    labels    = local.labels
  }
}

resource "kubernetes_role_v1" "bootstrap" {
  metadata {
    name      = "polaris-bootstrap"
    namespace = var.namespace
    labels    = local.labels
  }

  rule {
    api_groups = [""]
    resources  = ["secrets"]
    verbs      = ["create", "delete", "get", "patch", "update"]
  }
}

resource "kubernetes_role_binding_v1" "bootstrap" {
  metadata {
    name      = "polaris-bootstrap"
    namespace = var.namespace
    labels    = local.labels
  }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "Role"
    name      = kubernetes_role_v1.bootstrap.metadata[0].name
  }

  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account_v1.bootstrap.metadata[0].name
    namespace = var.namespace
  }
}

resource "kubernetes_job_v1" "bootstrap" {
  metadata {
    name      = "polaris-bootstrap-${helm_release.polaris.metadata.revision}"
    namespace = var.namespace
    labels    = local.labels
  }

  spec {
    backoff_limit = 3

    template {
      metadata {
        labels = merge(local.labels, {
          "openlakeforge.io/job" = "catalog-bootstrap"
        })
        annotations = local.bootstrap_annotations
      }

      spec {
        restart_policy       = "Never"
        service_account_name = kubernetes_service_account_v1.bootstrap.metadata[0].name

        container {
          name  = "bootstrap"
          image = var.bootstrap_job_image

          command = ["/bin/sh", "-ec"]
          args = [<<-SCRIPT
            set -eu

            polaris_url="http://${var.release_name}:8181"

            request() {
              method="$1"
              path="$2"
              expected_codes="$3"
              data="$${4:-}"

              if [ -n "$data" ]; then
                code="$(curl -sS -o /tmp/polaris-body -w '%%{http_code}' \
                  -X "$method" "$polaris_url/api/management/v1$path" \
                  -H "Authorization: Bearer $POLARIS_TOKEN" \
                  -H "Content-Type: application/json" \
                  -d "$data")"
              else
                code="$(curl -sS -o /tmp/polaris-body -w '%%{http_code}' \
                  -X "$method" "$polaris_url/api/management/v1$path" \
                  -H "Authorization: Bearer $POLARIS_TOKEN" \
                  -H "Content-Type: application/json")"
              fi

              case " $expected_codes " in
                *" $code "*) return 0 ;;
              esac

              echo "Polaris $method $path returned HTTP $code" >&2
              cat /tmp/polaris-body >&2 || true
              exit 1
            }

            token_response=""
            attempt=1
            while [ "$attempt" -le 60 ]; do
              if token_response="$(curl -sf -X POST "$polaris_url/api/catalog/v1/oauth/tokens" \
                -u "$ROOT_CLIENT_ID:$ROOT_CLIENT_SECRET" \
                -d "grant_type=client_credentials" \
                -d "scope=${local.oauth_scope}")"; then
                break
              fi
              sleep 5
              attempt=$((attempt + 1))
            done

            POLARIS_TOKEN="$(echo "$token_response" | sed -n 's/.*"access_token":"\([^"]*\)".*/\1/p')"

            if [ -z "$POLARIS_TOKEN" ]; then
              echo "Failed to obtain Polaris root token" >&2
              echo "$token_response" >&2
              exit 1
            fi

            request POST "/catalogs" "201 409" '{
              "name": "${var.catalog_name}",
              "type": "INTERNAL",
              "properties": {
                "default-base-location": "s3://${var.storage_contract.bucket_name}"
              },
              "storageConfigInfo": {
                "storageType": "S3",
                "allowedLocations": ["s3://${var.storage_contract.bucket_name}/"],
                "pathStyleAccess": true,
                "stsUnavailable": true
              }
            }'

            request DELETE "/principals/${var.principal_name}" "204 404"

            request POST "/principals" "201" '{"name": "${var.principal_name}", "type": "SERVICE"}'
            client_id="$(sed -n 's/.*"clientId":"\([^"]*\)".*/\1/p' /tmp/polaris-body | head -n 1)"
            client_secret="$(sed -n 's/.*"clientSecret":"\([^"]*\)".*/\1/p' /tmp/polaris-body | head -n 1)"

            if [ -z "$client_id" ] || [ -z "$client_secret" ]; then
              echo "Failed to parse Trino principal credentials" >&2
              cat /tmp/polaris-body >&2
              exit 1
            fi

            kubectl delete secret "${var.trino_credentials_secret_name}" -n "$NAMESPACE" --ignore-not-found
            kubectl create secret generic "${var.trino_credentials_secret_name}" \
              -n "$NAMESPACE" \
              --from-literal=POLARIS_TRINO_CLIENT_ID="$client_id" \
              --from-literal=POLARIS_TRINO_CLIENT_SECRET="$client_secret"

            request POST "/principal-roles" "201 409" '{"principalRole": {"name": "${var.principal_role}"}}'
            request PUT "/principals/${var.principal_name}/principal-roles" "201 409" '{"principalRole": {"name": "${var.principal_role}"}}'
            request POST "/catalogs/${var.catalog_name}/catalog-roles" "201 409" '{"catalogRole": {"name": "${var.catalog_role}"}}'
            request PUT "/catalogs/${var.catalog_name}/catalog-roles/${var.catalog_role}/grants" "201 409" '{"grant": {"type": "catalog", "privilege": "CATALOG_MANAGE_CONTENT"}}'
            request PUT "/principal-roles/${var.principal_role}/catalog-roles/${var.catalog_name}" "201 409" '{"catalogRole": {"name": "${var.catalog_role}"}}'
          SCRIPT
          ]

          env {
            name = "NAMESPACE"
            value_from {
              field_ref {
                field_path = "metadata.namespace"
              }
            }
          }

          env {
            name = "ROOT_CLIENT_ID"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.bootstrap_credentials.metadata[0].name
                key  = "ROOT_CLIENT_ID"
              }
            }
          }

          env {
            name = "ROOT_CLIENT_SECRET"
            value_from {
              secret_key_ref {
                name = kubernetes_secret_v1.bootstrap_credentials.metadata[0].name
                key  = "ROOT_CLIENT_SECRET"
              }
            }
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
    helm_release.polaris,
    kubernetes_role_binding_v1.bootstrap,
  ]

  lifecycle {
    replace_triggered_by = [
      terraform_data.polaris_release_revision,
    ]
  }
}
