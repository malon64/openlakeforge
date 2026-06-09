locals {
  labels = {
    "app.kubernetes.io/name"       = "openmetadata"
    "app.kubernetes.io/managed-by" = "terraform"
    "openlakeforge.io/component"   = "governance"
  }

  om_url                              = "http://${var.release_name}:${var.om_http_port}"
  openlineage_proxy_name              = "openmetadata-openlineage"
  openlineage_proxy_port              = 5000
  openlineage_proxy_target_port       = 8080
  openlineage_proxy_upstream_endpoint = "http://${var.release_name}:${var.om_http_port}/api/v1/openlineage/lineage"
  openlineage_proxy_labels = merge(local.labels, {
    "openlakeforge.io/service" = "openlineage-proxy"
  })
  catalog_schema_names_json     = jsonencode(var.catalog_schema_names)
  catalog_schema_names_json_b64 = base64encode(local.catalog_schema_names_json)
  bootstrap_annotations = {
    "openlakeforge.io/openmetadata-release-revision" = tostring(helm_release.openmetadata.metadata.revision)
    "openlakeforge.io/catalog-schema-hash"           = sha256(local.catalog_schema_names_json)
  }
}

# OpenSearch via the openmetadata-dependencies chart (Airflow + MySQL disabled)
resource "helm_release" "openmetadata_deps" {
  name       = "${var.release_name}-deps"
  repository = var.chart_repository
  chart      = "openmetadata-dependencies"
  version    = var.deps_chart_version
  namespace  = var.namespace

  wait    = true
  timeout = 1200

  values = [
    file(var.deps_values_file),
  ]
}

# Main OpenMetadata application
resource "helm_release" "openmetadata" {
  name       = var.release_name
  repository = var.chart_repository
  chart      = "openmetadata"
  version    = var.chart_version
  namespace  = var.namespace

  wait            = true
  timeout         = 600
  cleanup_on_fail = true
  upgrade_install = true

  values = [
    file(var.base_values_file),
    yamlencode({
      openmetadata = {
        config = {
          database = {
            host         = var.postgresql_contract.host
            port         = var.postgresql_contract.port
            driverClass  = "org.postgresql.Driver"
            dbScheme     = "postgresql"
            databaseName = var.postgresql_contract.openmetadata_db_name
            auth = {
              username = var.postgresql_contract.openmetadata_db_user
              password = {
                secretRef = var.postgresql_contract.openmetadata_credentials_secret_name
                secretKey = "postgresql-password"
              }
            }
            dbParams = "sslmode=disable"
          }
          pipelineServiceClientConfig = {
            k8s = {
              namespace = var.namespace
            }
          }
        }
      }
    }),
  ]

  depends_on = [
    helm_release.openmetadata_deps,
  ]
}

resource "terraform_data" "openmetadata_release_revision" {
  triggers_replace = [
    helm_release.openmetadata.metadata.revision,
  ]
}

resource "terraform_data" "openmetadata_catalog_schemas" {
  triggers_replace = [
    var.catalog_database_name,
    sha256(local.catalog_schema_names_json),
  ]
}

resource "kubernetes_config_map_v1" "openlineage_proxy" {
  metadata {
    name      = local.openlineage_proxy_name
    namespace = var.namespace
    labels    = local.openlineage_proxy_labels
  }

  data = {
    "proxy.py" = <<-PY
      from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
      import json
      import os
      import urllib.error
      import urllib.request
      import uuid

      PORT = int(os.environ.get("PORT", "${local.openlineage_proxy_target_port}"))
      UPSTREAM = os.environ["OPENMETADATA_OPENLINEAGE_URL"]
      TOKEN = os.environ["OPENMETADATA_INGESTION_BOT_JWT"]


      def normalize_openlineage_payload(raw):
          try:
              payload = json.loads(raw)
          except json.JSONDecodeError:
              return raw

          run = payload.get("run")
          run_id = run.get("runId") if isinstance(run, dict) else None
          if isinstance(run_id, str):
              try:
                  uuid.UUID(run_id)
              except ValueError:
                  run["runId"] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"openlakeforge:floe:{run_id}"))

          return json.dumps(payload, separators=(",", ":")).encode("utf-8")


      class Handler(BaseHTTPRequestHandler):
          def do_GET(self):
              if self.path == "/healthz":
                  self.send_response(200)
                  self.send_header("Content-Type", "text/plain")
                  self.end_headers()
                  self.wfile.write(b"ok\n")
                  return
              self.send_response(404)
              self.end_headers()

          def do_POST(self):
              if self.path != "/api/v1/lineage":
                  self.send_response(404)
                  self.end_headers()
                  return

              length = int(self.headers.get("Content-Length", "0"))
              body = normalize_openlineage_payload(self.rfile.read(length))
              request = urllib.request.Request(
                  UPSTREAM,
                  data=body,
                  method="POST",
                  headers={
                      "Authorization": f"Bearer {TOKEN}",
                      "Content-Type": self.headers.get("Content-Type", "application/json"),
                      "User-Agent": self.headers.get("User-Agent", ""),
                  },
              )

              try:
                  with urllib.request.urlopen(request, timeout=30) as response:
                      status = response.status
                      response_body = response.read()
                      response_content_type = response.headers.get("Content-Type", "application/json")
              except urllib.error.HTTPError as err:
                  status = err.code
                  response_body = err.read()
                  response_content_type = err.headers.get("Content-Type", "application/json")

              self.send_response(status)
              self.send_header("Content-Type", response_content_type)
              self.send_header("Content-Length", str(len(response_body)))
              self.end_headers()
              self.wfile.write(response_body)

          def log_message(self, fmt, *args):
              print("%s - %s" % (self.address_string(), fmt % args), flush=True)


      ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
    PY
  }
}

resource "kubernetes_deployment_v1" "openlineage_proxy" {
  metadata {
    name      = local.openlineage_proxy_name
    namespace = var.namespace
    labels    = local.openlineage_proxy_labels
  }

  spec {
    replicas = 1

    selector {
      match_labels = local.openlineage_proxy_labels
    }

    template {
      metadata {
        labels = local.openlineage_proxy_labels
        annotations = {
          "openlakeforge.io/config-hash"           = sha256(kubernetes_config_map_v1.openlineage_proxy.data["proxy.py"])
          "openlakeforge.io/om-bootstrap-revision" = tostring(helm_release.openmetadata.metadata.revision)
        }
      }

      spec {
        container {
          name  = "proxy"
          image = "python:3.12-alpine"

          command = ["python", "/app/proxy.py"]

          port {
            name           = "http"
            container_port = local.openlineage_proxy_target_port
          }

          env {
            name  = "OPENMETADATA_OPENLINEAGE_URL"
            value = local.openlineage_proxy_upstream_endpoint
          }

          env {
            name = "OPENMETADATA_INGESTION_BOT_JWT"
            value_from {
              secret_key_ref {
                name = var.ingestion_bot_secret_name
                key  = var.ingestion_bot_jwt_key
              }
            }
          }

          volume_mount {
            name       = "config"
            mount_path = "/app/proxy.py"
            sub_path   = "proxy.py"
            read_only  = true
          }

          readiness_probe {
            http_get {
              path = "/healthz"
              port = "http"
            }
          }

          liveness_probe {
            http_get {
              path = "/healthz"
              port = "http"
            }
          }

          resources {
            requests = {
              cpu    = "25m"
              memory = "32Mi"
            }
            limits = {
              memory = "64Mi"
            }
          }
        }

        volume {
          name = "config"
          config_map {
            name = kubernetes_config_map_v1.openlineage_proxy.metadata[0].name
          }
        }
      }
    }
  }

  depends_on = [
    kubernetes_job_v1.bootstrap,
  ]
}

resource "kubernetes_service_v1" "openlineage_proxy" {
  metadata {
    name      = local.openlineage_proxy_name
    namespace = var.namespace
    labels    = local.openlineage_proxy_labels
  }

  spec {
    selector = local.openlineage_proxy_labels

    port {
      name        = "http"
      port        = local.openlineage_proxy_port
      target_port = "http"
    }

    type = "ClusterIP"
  }
}

# ServiceAccount + RBAC for the bootstrap job
resource "kubernetes_service_account_v1" "bootstrap" {
  metadata {
    name      = "openmetadata-bootstrap"
    namespace = var.namespace
    labels    = local.labels
  }
}

resource "kubernetes_role_v1" "bootstrap" {
  metadata {
    name      = "openmetadata-bootstrap"
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
    name      = "openmetadata-bootstrap"
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
    name      = "openmetadata-bootstrap-${helm_release.openmetadata.metadata.revision}"
    namespace = var.namespace
    labels    = local.labels
  }

  spec {
    backoff_limit = 3

    template {
      metadata {
        labels = merge(local.labels, {
          "openlakeforge.io/job" = "governance-bootstrap"
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

            om_url="${local.om_url}"
            admin_email="${var.admin_email}"
            admin_password="${var.admin_password}"

            # Wait for OpenMetadata to become healthy
            attempt=1
            while [ "$attempt" -le 120 ]; do
              code="$(curl -so /dev/null -w '%%{http_code}' "$om_url/api/v1/system/config/jwks")"
              if [ "$code" = "200" ]; then
                break
              fi
              sleep 5
              attempt=$((attempt + 1))
            done
            if [ "$attempt" -gt 120 ]; then
              echo "OpenMetadata did not become healthy in time" >&2
              exit 1
            fi

            # Login as admin. OpenMetadata Basic Auth expects the password base64-encoded.
            encoded_password="$(printf '%s' "$admin_password" | base64 | tr -d '\n')"
            login_resp="$(curl -sS -X POST "$om_url/api/v1/users/login" \
              -H "Content-Type: application/json" \
              -d "{\"email\":\"$admin_email\",\"password\":\"$encoded_password\"}")"
            ADMIN_JWT="$(echo "$login_resp" | jq -r '.accessToken // empty')"

            if [ -z "$ADMIN_JWT" ]; then
              echo "Failed to obtain admin JWT" >&2
              echo "$login_resp" >&2
              exit 1
            fi

            # Generate an unlimited ingestion-bot JWT token for local machine traffic.
            bot_resp="$(curl -sS "$om_url/api/v1/users/name/ingestion-bot" \
              -H "Authorization: Bearer $ADMIN_JWT")"
            bot_id="$(echo "$bot_resp" | jq -r '.id // empty')"

            if [ -z "$bot_id" ]; then
              echo "Failed to find ingestion-bot user" >&2
              echo "$bot_resp" >&2
              exit 1
            fi

            token_resp="$(curl -sS -X PUT "$om_url/api/v1/users/generateToken/$bot_id" \
              -H "Authorization: Bearer $ADMIN_JWT" \
              -H "Content-Type: application/json" \
              -d "{\"JWTTokenExpiry\":\"Unlimited\"}")"
            token_get_resp="$(curl -sS "$om_url/api/v1/users/token/$bot_id" \
              -H "Authorization: Bearer $ADMIN_JWT")"
            BOT_JWT="$(echo "$token_get_resp" | jq -r '.JWTToken // empty')"

            if [ -z "$BOT_JWT" ]; then
              echo "Failed to obtain ingestion-bot JWT" >&2
              echo "$token_resp" >&2
              echo "$token_get_resp" >&2
              exit 1
            fi
            case "$BOT_JWT" in
              fernet:*)
                echo "OpenMetadata returned an encrypted ingestion-bot token instead of a bearer JWT" >&2
                exit 1
                ;;
            esac

            # OpenMetadata 1.12's Iceberg connector uses PyIceberg 0.5.1, whose
            # client-credentials flow hardcodes an OAuth scope Polaris rejects.
            # Mint the Polaris token here with the correct local scope and store
            # the bearer token in the OpenMetadata service connection.
            polaris_token_code="$(curl -sS -o /tmp/polaris-token-body -w '%%{http_code}' \
              -X POST "${var.catalog_contract.token_uri}" \
              -H "Content-Type: application/x-www-form-urlencoded" \
              -d "grant_type=client_credentials" \
              -d "client_id=$POLARIS_OM_CLIENT_ID" \
              -d "client_secret=$POLARIS_OM_CLIENT_SECRET" \
              -d "scope=${var.catalog_contract.oauth_scope}")"
            if [ "$polaris_token_code" != "200" ]; then
              echo "Failed to obtain Polaris token for OpenMetadata (HTTP $polaris_token_code)" >&2
              cat /tmp/polaris-token-body >&2
              exit 1
            fi

            POLARIS_OM_TOKEN="$(jq -r '.access_token // empty' /tmp/polaris-token-body)"
            if [ -z "$POLARIS_OM_TOKEN" ]; then
              echo "Failed to parse Polaris token for OpenMetadata" >&2
              cat /tmp/polaris-token-body >&2
              exit 1
            fi

            # Store bot JWT as Kubernetes Secret
            kubectl delete secret "${var.ingestion_bot_secret_name}" -n "$NAMESPACE" --ignore-not-found
            kubectl create secret generic "${var.ingestion_bot_secret_name}" \
              -n "$NAMESPACE" \
              --from-literal="${var.ingestion_bot_jwt_key}=$BOT_JWT"

            # Create or update the Polaris Iceberg database service in OpenMetadata.
            svc_code="$(curl -sS -o /tmp/om-svc-body -w '%%{http_code}' \
              -X PUT "$om_url/api/v1/services/databaseServices" \
              -H "Authorization: Bearer $ADMIN_JWT" \
              -H "Content-Type: application/json" \
              -d "{
                \"name\": \"polaris\",
                \"displayName\": \"Polaris Iceberg Catalog\",
                \"serviceType\": \"Iceberg\",
                \"connection\": {
                  \"config\": {
                    \"type\": \"Iceberg\",
                    \"catalog\": {
                      \"name\": \"${var.catalog_contract.warehouse}\",
                      \"databaseName\": \"${var.catalog_database_name}\",
                      \"warehouseLocation\": \"${var.catalog_contract.warehouse}\",
                      \"connection\": {
                        \"uri\": \"${var.catalog_contract.rest_uri}\",
                        \"token\": \"$POLARIS_OM_TOKEN\",
                        \"fileSystem\": {
                          \"type\": {
                            \"awsAccessKeyId\": \"$AWS_ACCESS_KEY_ID\",
                            \"awsSecretAccessKey\": \"$AWS_SECRET_ACCESS_KEY\",
                            \"awsRegion\": \"${var.storage_contract.region}\",
                            \"endPointURL\": \"${var.storage_contract.virtual_host_endpoint}\"
                          }
                        }
                      }
                    }
                  }
                }
              }")"

            case " 200 201 " in
              *" $svc_code "*) svc_resp="$(cat /tmp/om-svc-body)" ;;
              *)
                echo "Failed to create or update OpenMetadata database service (HTTP $svc_code)" >&2
                cat /tmp/om-svc-body >&2
                exit 1
                ;;
            esac

            svc_id="$(echo "$svc_resp" | jq -r '.id // empty')"

            if [ -z "$svc_id" ]; then
              echo "Failed to retrieve polaris service id" >&2
              echo "$svc_resp" >&2
              exit 1
            fi

            catalog_database="${var.catalog_database_name}"
            catalog_database_fqn="polaris.$catalog_database"
            db_payload="$(jq -n \
              --arg name "$catalog_database" \
              --arg service "polaris" \
              '{
                name: $name,
                service: $service
              }')"
            db_code="$(curl -sS -o /tmp/om-db-body -w '%%{http_code}' \
              -X PUT "$om_url/api/v1/databases" \
              -H "Authorization: Bearer $ADMIN_JWT" \
              -H "Content-Type: application/json" \
              -d "$db_payload")"
            case " 200 201 " in
              *" $db_code "*) ;;
              *)
                echo "Failed to create or update OpenMetadata database '$catalog_database_fqn' (HTTP $db_code)" >&2
                cat /tmp/om-db-body >&2
                exit 1
                ;;
            esac

            printf '%s' "${local.catalog_schema_names_json_b64}" | base64 -d >/tmp/om-catalog-schemas.json
            jq -r '.[]' /tmp/om-catalog-schemas.json | while IFS= read -r schema_name; do
              schema_payload="$(jq -n \
                --arg name "$schema_name" \
                --arg database "$catalog_database_fqn" \
                '{
                  name: $name,
                  database: $database
                }')"
              schema_code="$(curl -sS -o /tmp/om-schema-body -w '%%{http_code}' \
                -X PUT "$om_url/api/v1/databaseSchemas" \
                -H "Authorization: Bearer $ADMIN_JWT" \
                -H "Content-Type: application/json" \
                -d "$schema_payload")"
              case " 200 201 " in
                *" $schema_code "*) ;;
                *)
                  echo "Failed to create or update OpenMetadata schema '$catalog_database_fqn.$schema_name' (HTTP $schema_code)" >&2
                  cat /tmp/om-schema-body >&2
                  exit 1
                  ;;
              esac
            done

            pipeline_service_code="$(curl -sS -o /tmp/om-pipeline-service-body -w '%%{http_code}' \
              -X PUT "$om_url/api/v1/services/pipelineServices" \
              -H "Authorization: Bearer $ADMIN_JWT" \
              -H "Content-Type: application/json" \
              -d "{
                \"name\": \"openlineage\",
                \"displayName\": \"OpenLineage Events\",
                \"serviceType\": \"CustomPipeline\",
                \"connection\": {
                  \"config\": {
                    \"type\": \"CustomPipeline\"
                  }
                }
              }")"
            case " 200 201 " in
              *" $pipeline_service_code "*) ;;
              *)
                echo "Failed to create or update OpenLineage pipeline service (HTTP $pipeline_service_code)" >&2
                cat /tmp/om-pipeline-service-body >&2
                exit 1
                ;;
            esac

            dbt_pipeline_code="$(curl -sS -o /tmp/om-dbt-pipeline-body -w '%%{http_code}' \
              -X PUT "$om_url/api/v1/pipelines" \
              -H "Authorization: Bearer $ADMIN_JWT" \
              -H "Content-Type: application/json" \
              -d "{
                \"name\": \"dbt-dbt-run-sales_poc\",
                \"displayName\": \"dbt Sales POC\",
                \"service\": \"openlineage\",
                \"scheduleInterval\": \"manual\",
                \"tasks\": [
                  {
                    \"name\": \"dbt-build\"
                  }
                ]
              }")"
            case " 200 201 " in
              *" $dbt_pipeline_code "*) ;;
              *)
                echo "Failed to create or update dbt OpenLineage pipeline (HTTP $dbt_pipeline_code)" >&2
                cat /tmp/om-dbt-pipeline-body >&2
                exit 1
                ;;
            esac

            # Create or reuse the metadata ingestion pipeline.
            pipeline_fqn="polaris.polaris-metadata-ingestion"
            pipeline_code="$(curl -sS -o /tmp/om-pipeline-body -w '%%{http_code}' \
              "$om_url/api/v1/services/ingestionPipelines/name/$pipeline_fqn" \
              -H "Authorization: Bearer $ADMIN_JWT")"
            if [ "$pipeline_code" = "200" ]; then
              pipeline_resp="$(cat /tmp/om-pipeline-body)"
            elif [ "$pipeline_code" = "404" ]; then
              pipeline_code="$(curl -sS -o /tmp/om-pipeline-body -w '%%{http_code}' \
                -X POST "$om_url/api/v1/services/ingestionPipelines" \
                -H "Authorization: Bearer $ADMIN_JWT" \
                -H "Content-Type: application/json" \
                -d "{
                  \"name\": \"polaris-metadata-ingestion\",
                  \"displayName\": \"Polaris Metadata Ingestion\",
                  \"pipelineType\": \"metadata\",
                  \"sourceConfig\": {
                    \"config\": {
                      \"type\": \"DatabaseMetadata\",
                      \"includeViews\": false,
                      \"includeTags\": false
                    }
                  },
	                  \"airflowConfig\": {
	                    \"startDate\": \"2025-01-01T00:00:00.000Z\",
	                    \"retries\": 1,
	                    \"pausePipeline\": true
	                  },
	                  \"service\": {
	                    \"id\": \"$svc_id\",
	                    \"type\": \"databaseService\"
	                  }
	                }")"

              case " 200 201 " in
                *" $pipeline_code "*) pipeline_resp="$(cat /tmp/om-pipeline-body)" ;;
                *)
                  echo "Failed to create ingestion pipeline (HTTP $pipeline_code)" >&2
                  cat /tmp/om-pipeline-body >&2
                  exit 1
                  ;;
              esac
            else
              echo "Failed to inspect ingestion pipeline (HTTP $pipeline_code)" >&2
              cat /tmp/om-pipeline-body >&2
              exit 1
            fi

            pipeline_id="$(echo "$pipeline_resp" | jq -r '.id // empty')"

            if [ -z "$pipeline_id" ]; then
              echo "Failed to retrieve ingestion pipeline id" >&2
              exit 1
            fi

            # Deploy and trigger the first Polaris crawl.
            deploy_code="$(curl -sS -o /tmp/om-deploy-body -w '%%{http_code}' \
              -X POST "$om_url/api/v1/services/ingestionPipelines/deploy/$pipeline_id" \
              -H "Authorization: Bearer $ADMIN_JWT")"
            case " 200 201 " in
              *" $deploy_code "*) ;;
              *)
                echo "Failed to deploy ingestion pipeline (HTTP $deploy_code)" >&2
                cat /tmp/om-deploy-body >&2
                exit 1
                ;;
            esac

            trigger_code="$(curl -sS -o /tmp/om-trigger-body -w '%%{http_code}' \
              -X POST "$om_url/api/v1/services/ingestionPipelines/trigger/$pipeline_id" \
              -H "Authorization: Bearer $ADMIN_JWT")"
            case " 200 201 202 " in
              *" $trigger_code "*) ;;
              *)
                echo "Failed to trigger ingestion pipeline (HTTP $trigger_code)" >&2
                cat /tmp/om-trigger-body >&2
                exit 1
                ;;
            esac

            echo "OpenMetadata bootstrap complete."
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
            name = "POLARIS_OM_CLIENT_ID"
            value_from {
              secret_key_ref {
                name = var.catalog_contract.om_credentials_secret_name
                key  = var.catalog_contract.om_client_id_key
              }
            }
          }

          env {
            name = "POLARIS_OM_CLIENT_SECRET"
            value_from {
              secret_key_ref {
                name = var.catalog_contract.om_credentials_secret_name
                key  = var.catalog_contract.om_client_secret_key
              }
            }
          }

          env {
            name = "AWS_ACCESS_KEY_ID"
            value_from {
              secret_key_ref {
                name = var.storage_contract.credentials_secret_name
                key  = var.storage_contract.access_key_id_key
              }
            }
          }

          env {
            name = "AWS_SECRET_ACCESS_KEY"
            value_from {
              secret_key_ref {
                name = var.storage_contract.credentials_secret_name
                key  = var.storage_contract.secret_access_key_key
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
    helm_release.openmetadata,
    kubernetes_role_binding_v1.bootstrap,
  ]

  lifecycle {
    replace_triggered_by = [
      terraform_data.openmetadata_release_revision,
      terraform_data.openmetadata_catalog_schemas,
    ]
  }
}

resource "kubernetes_cron_job_v1" "catalog_refresh" {
  count = var.catalog_refresh_enabled ? 1 : 0

  metadata {
    name      = "openmetadata-polaris-refresh"
    namespace = var.namespace
    labels = merge(local.labels, {
      "openlakeforge.io/job" = "catalog-refresh"
    })
  }

  spec {
    schedule                      = var.catalog_refresh_schedule
    concurrency_policy            = "Forbid"
    successful_jobs_history_limit = 3
    failed_jobs_history_limit     = 3

    job_template {
      metadata {
        labels = merge(local.labels, {
          "openlakeforge.io/job" = "catalog-refresh"
        })
      }

      spec {
        backoff_limit = 1

        template {
          metadata {
            labels = merge(local.labels, {
              "openlakeforge.io/job" = "catalog-refresh"
            })
          }

          spec {
            restart_policy = "Never"

            container {
              name  = "catalog-refresh"
              image = var.bootstrap_job_image

              command = ["/bin/sh", "-ec"]
              args = [<<-SCRIPT
                set -eu

                om_url="${local.om_url}"
                admin_email="${var.admin_email}"
                admin_password="${var.admin_password}"

                encoded_password="$(printf '%s' "$admin_password" | base64 | tr -d '\n')"
                login_resp="$(curl -sS -X POST "$om_url/api/v1/users/login" \
                  -H "Content-Type: application/json" \
                  -d "{\"email\":\"$admin_email\",\"password\":\"$encoded_password\"}")"
                ADMIN_JWT="$(echo "$login_resp" | jq -r '.accessToken // empty')"
                if [ -z "$ADMIN_JWT" ]; then
                  echo "Failed to obtain admin JWT" >&2
                  echo "$login_resp" >&2
                  exit 1
                fi

                polaris_token_code="$(curl -sS -o /tmp/polaris-token-body -w '%%{http_code}' \
                  -X POST "${var.catalog_contract.token_uri}" \
                  -H "Content-Type: application/x-www-form-urlencoded" \
                  -d "grant_type=client_credentials" \
                  -d "client_id=$POLARIS_OM_CLIENT_ID" \
                  -d "client_secret=$POLARIS_OM_CLIENT_SECRET" \
                  -d "scope=${var.catalog_contract.oauth_scope}")"
                if [ "$polaris_token_code" != "200" ]; then
                  echo "Failed to obtain Polaris token for OpenMetadata (HTTP $polaris_token_code)" >&2
                  cat /tmp/polaris-token-body >&2
                  exit 1
                fi

                POLARIS_OM_TOKEN="$(jq -r '.access_token // empty' /tmp/polaris-token-body)"
                if [ -z "$POLARIS_OM_TOKEN" ]; then
                  echo "Failed to parse Polaris token for OpenMetadata" >&2
                  cat /tmp/polaris-token-body >&2
                  exit 1
                fi

                svc_code="$(curl -sS -o /tmp/om-svc-body -w '%%{http_code}' \
                  -X PUT "$om_url/api/v1/services/databaseServices" \
                  -H "Authorization: Bearer $ADMIN_JWT" \
                  -H "Content-Type: application/json" \
                  -d "{
                    \"name\": \"polaris\",
                    \"displayName\": \"Polaris Iceberg Catalog\",
                    \"serviceType\": \"Iceberg\",
                    \"connection\": {
                      \"config\": {
                        \"type\": \"Iceberg\",
                        \"catalog\": {
                          \"name\": \"${var.catalog_contract.warehouse}\",
                          \"warehouseLocation\": \"${var.catalog_contract.warehouse}\",
                          \"connection\": {
                            \"uri\": \"${var.catalog_contract.rest_uri}\",
                            \"token\": \"$POLARIS_OM_TOKEN\",
                            \"fileSystem\": {
                              \"type\": {
                                \"awsAccessKeyId\": \"$AWS_ACCESS_KEY_ID\",
                                \"awsSecretAccessKey\": \"$AWS_SECRET_ACCESS_KEY\",
                                \"awsRegion\": \"${var.storage_contract.region}\",
                                \"endPointURL\": \"${var.storage_contract.virtual_host_endpoint}\"
                              }
                            }
                          }
                        }
                      }
                    }
                  }")"
                case " 200 201 " in
                  *" $svc_code "*) ;;
                  *)
                    echo "Failed to create or update OpenMetadata database service (HTTP $svc_code)" >&2
                    cat /tmp/om-svc-body >&2
                    exit 1
                    ;;
                esac

                catalog_database="${var.catalog_database_name}"
                catalog_database_fqn="polaris.$catalog_database"
                db_payload="$(jq -n \
                  --arg name "$catalog_database" \
                  --arg service "polaris" \
                  '{name: $name, service: $service}')"
                db_code="$(curl -sS -o /tmp/om-db-body -w '%%{http_code}' \
                  -X PUT "$om_url/api/v1/databases" \
                  -H "Authorization: Bearer $ADMIN_JWT" \
                  -H "Content-Type: application/json" \
                  -d "$db_payload")"
                case " 200 201 " in
                  *" $db_code "*) ;;
                  *)
                    echo "Failed to create or update OpenMetadata database '$catalog_database_fqn' (HTTP $db_code)" >&2
                    cat /tmp/om-db-body >&2
                    exit 1
                    ;;
                esac

                printf '%s' "${local.catalog_schema_names_json_b64}" | base64 -d >/tmp/om-catalog-schemas.json
                jq -r '.[]' /tmp/om-catalog-schemas.json | while IFS= read -r schema_name; do
                  schema_payload="$(jq -n \
                    --arg name "$schema_name" \
                    --arg database "$catalog_database_fqn" \
                    '{name: $name, database: $database}')"
                  schema_code="$(curl -sS -o /tmp/om-schema-body -w '%%{http_code}' \
                    -X PUT "$om_url/api/v1/databaseSchemas" \
                    -H "Authorization: Bearer $ADMIN_JWT" \
                    -H "Content-Type: application/json" \
                    -d "$schema_payload")"
                  case " 200 201 " in
                    *" $schema_code "*) ;;
                    *)
                      echo "Failed to create or update OpenMetadata schema '$catalog_database_fqn.$schema_name' (HTTP $schema_code)" >&2
                      cat /tmp/om-schema-body >&2
                      exit 1
                      ;;
                  esac
                done

                pipeline_service_code="$(curl -sS -o /tmp/om-pipeline-service-body -w '%%{http_code}' \
                  -X PUT "$om_url/api/v1/services/pipelineServices" \
                  -H "Authorization: Bearer $ADMIN_JWT" \
                  -H "Content-Type: application/json" \
                  -d "{
                    \"name\": \"openlineage\",
                    \"displayName\": \"OpenLineage Events\",
                    \"serviceType\": \"CustomPipeline\",
                    \"connection\": {
                      \"config\": {
                        \"type\": \"CustomPipeline\"
                      }
                    }
                  }")"
                case " 200 201 " in
                  *" $pipeline_service_code "*) ;;
                  *)
                    echo "Failed to create or update OpenLineage pipeline service (HTTP $pipeline_service_code)" >&2
                    cat /tmp/om-pipeline-service-body >&2
                    exit 1
                    ;;
                esac

                dbt_pipeline_code="$(curl -sS -o /tmp/om-dbt-pipeline-body -w '%%{http_code}' \
                  -X PUT "$om_url/api/v1/pipelines" \
                  -H "Authorization: Bearer $ADMIN_JWT" \
                  -H "Content-Type: application/json" \
                  -d "{
                    \"name\": \"dbt-dbt-run-sales_poc\",
                    \"displayName\": \"dbt Sales POC\",
                    \"service\": \"openlineage\",
                    \"scheduleInterval\": \"manual\",
                    \"tasks\": [
                      {
                        \"name\": \"dbt-build\"
                      }
                    ]
                  }")"
                case " 200 201 " in
                  *" $dbt_pipeline_code "*) ;;
                  *)
                    echo "Failed to create or update dbt OpenLineage pipeline (HTTP $dbt_pipeline_code)" >&2
                    cat /tmp/om-dbt-pipeline-body >&2
                    exit 1
                    ;;
                esac

                pipeline_fqn="polaris.polaris-metadata-ingestion"
                pipeline_resp="$(curl -sS "$om_url/api/v1/services/ingestionPipelines/name/$pipeline_fqn" \
                  -H "Authorization: Bearer $ADMIN_JWT")"
                pipeline_id="$(echo "$pipeline_resp" | jq -r '.id // empty')"
                if [ -z "$pipeline_id" ]; then
                  echo "Failed to retrieve ingestion pipeline id" >&2
                  echo "$pipeline_resp" >&2
                  exit 1
                fi

                deploy_code="$(curl -sS -o /tmp/om-deploy-body -w '%%{http_code}' \
                  -X POST "$om_url/api/v1/services/ingestionPipelines/deploy/$pipeline_id" \
                  -H "Authorization: Bearer $ADMIN_JWT")"
                case " 200 201 " in
                  *" $deploy_code "*) ;;
                  *)
                    echo "Failed to deploy ingestion pipeline (HTTP $deploy_code)" >&2
                    cat /tmp/om-deploy-body >&2
                    exit 1
                    ;;
                esac

                trigger_code="$(curl -sS -o /tmp/om-trigger-body -w '%%{http_code}' \
                  -X POST "$om_url/api/v1/services/ingestionPipelines/trigger/$pipeline_id" \
                  -H "Authorization: Bearer $ADMIN_JWT")"
                case " 200 201 202 " in
                  *" $trigger_code "*) ;;
                  *)
                    echo "Failed to trigger ingestion pipeline (HTTP $trigger_code)" >&2
                    cat /tmp/om-trigger-body >&2
                    exit 1
                    ;;
                esac

                echo "OpenMetadata Polaris catalog refresh triggered."
              SCRIPT
              ]

              env {
                name = "POLARIS_OM_CLIENT_ID"
                value_from {
                  secret_key_ref {
                    name = var.catalog_contract.om_credentials_secret_name
                    key  = var.catalog_contract.om_client_id_key
                  }
                }
              }

              env {
                name = "POLARIS_OM_CLIENT_SECRET"
                value_from {
                  secret_key_ref {
                    name = var.catalog_contract.om_credentials_secret_name
                    key  = var.catalog_contract.om_client_secret_key
                  }
                }
              }

              env {
                name = "AWS_ACCESS_KEY_ID"
                value_from {
                  secret_key_ref {
                    name = var.storage_contract.credentials_secret_name
                    key  = var.storage_contract.access_key_id_key
                  }
                }
              }

              env {
                name = "AWS_SECRET_ACCESS_KEY"
                value_from {
                  secret_key_ref {
                    name = var.storage_contract.credentials_secret_name
                    key  = var.storage_contract.secret_access_key_key
                  }
                }
              }
            }
          }
        }
      }
    }
  }

  depends_on = [
    kubernetes_job_v1.bootstrap,
  ]
}
