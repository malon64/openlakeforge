locals {
  labels = {
    "app.kubernetes.io/name"       = "openmetadata"
    "app.kubernetes.io/managed-by" = "terraform"
    "openlakeforge.io/component"   = "governance"
  }

  om_url                        = "http://${var.release_name}:${var.om_http_port}"
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

            # Register Dagster as a Pipeline Service and trigger metadata ingestion.
            dagster_svc_code="$(curl -sS -o /tmp/om-dagster-svc-body -w '%%{http_code}' \
              -X PUT "$om_url/api/v1/services/pipelineServices" \
              -H "Authorization: Bearer $ADMIN_JWT" \
              -H "Content-Type: application/json" \
              -d "{
                \"name\": \"dagster\",
                \"displayName\": \"Dagster Orchestrator\",
                \"serviceType\": \"Dagster\",
                \"connection\": {
                  \"config\": {
                    \"type\": \"Dagster\",
                    \"host\": \"${var.dagster_webserver_url}\",
                    \"timeout\": 1000
                  }
                }
              }")"
            case " 200 201 " in
              *" $dagster_svc_code "*) dagster_svc_id="$(jq -r '.id // empty' /tmp/om-dagster-svc-body)" ;;
              *)
                echo "Failed to create Dagster pipeline service (HTTP $dagster_svc_code)" >&2
                cat /tmp/om-dagster-svc-body >&2
                exit 1
                ;;
            esac

            dagster_pipeline_fqn="dagster.dagster-metadata-ingestion"
            dagster_check_code="$(curl -sS -o /tmp/om-dagster-pipeline-body -w '%%{http_code}' \
              "$om_url/api/v1/services/ingestionPipelines/name/$dagster_pipeline_fqn" \
              -H "Authorization: Bearer $ADMIN_JWT")"
            if [ "$dagster_check_code" = "200" ]; then
              dagster_pipeline_id="$(jq -r '.id // empty' /tmp/om-dagster-pipeline-body)"
            elif [ "$dagster_check_code" = "404" ]; then
              dagster_create_code="$(curl -sS -o /tmp/om-dagster-pipeline-body -w '%%{http_code}' \
                -X POST "$om_url/api/v1/services/ingestionPipelines" \
                -H "Authorization: Bearer $ADMIN_JWT" \
                -H "Content-Type: application/json" \
                -d "{
                  \"name\": \"dagster-metadata-ingestion\",
                  \"displayName\": \"Dagster Pipeline Metadata\",
                  \"pipelineType\": \"metadata\",
                  \"sourceConfig\": {
                    \"config\": {
                      \"type\": \"PipelineMetadata\",
                      \"includeLineage\": true
                    }
                  },
                  \"airflowConfig\": {
                    \"startDate\": \"2025-01-01T00:00:00.000Z\",
                    \"retries\": 1,
                    \"pausePipeline\": true
                  },
                  \"service\": {
                    \"id\": \"$dagster_svc_id\",
                    \"type\": \"pipelineService\"
                  }
                }")"
              case " 200 201 " in
                *" $dagster_create_code "*) dagster_pipeline_id="$(jq -r '.id // empty' /tmp/om-dagster-pipeline-body)" ;;
                *)
                  echo "Failed to create Dagster ingestion pipeline (HTTP $dagster_create_code)" >&2
                  cat /tmp/om-dagster-pipeline-body >&2
                  exit 1
                  ;;
              esac
            else
              echo "Failed to inspect Dagster ingestion pipeline (HTTP $dagster_check_code)" >&2
              exit 1
            fi

            curl -sS -o /tmp/om-dagster-deploy-body -X POST \
              "$om_url/api/v1/services/ingestionPipelines/deploy/$dagster_pipeline_id" \
              -H "Authorization: Bearer $ADMIN_JWT" || true
            curl -sS -o /tmp/om-dagster-trigger-body -X POST \
              "$om_url/api/v1/services/ingestionPipelines/trigger/$dagster_pipeline_id" \
              -H "Authorization: Bearer $ADMIN_JWT" || true
            echo "Dagster pipeline service registered."

            # Register Superset as a Dashboard Service and trigger metadata ingestion.
            superset_svc_code="$(curl -sS -o /tmp/om-superset-svc-body -w '%%{http_code}' \
              -X PUT "$om_url/api/v1/services/dashboardServices" \
              -H "Authorization: Bearer $ADMIN_JWT" \
              -H "Content-Type: application/json" \
              -d "{
                \"name\": \"superset\",
                \"displayName\": \"Superset Reporting\",
                \"serviceType\": \"Superset\",
                \"connection\": {
                  \"config\": {
                    \"type\": \"Superset\",
                    \"hostPort\": \"${var.superset_url}\",
                    \"username\": \"${var.superset_admin_username}\",
                    \"password\": \"$SUPERSET_ADMIN_PASSWORD\"
                  }
                }
              }")"
            case " 200 201 " in
              *" $superset_svc_code "*) superset_svc_id="$(jq -r '.id // empty' /tmp/om-superset-svc-body)" ;;
              *)
                echo "Failed to create Superset dashboard service (HTTP $superset_svc_code)" >&2
                cat /tmp/om-superset-svc-body >&2
                exit 1
                ;;
            esac

            superset_pipeline_fqn="superset.superset-metadata-ingestion"
            superset_check_code="$(curl -sS -o /tmp/om-superset-pipeline-body -w '%%{http_code}' \
              "$om_url/api/v1/services/ingestionPipelines/name/$superset_pipeline_fqn" \
              -H "Authorization: Bearer $ADMIN_JWT")"
            if [ "$superset_check_code" = "200" ]; then
              superset_pipeline_id="$(jq -r '.id // empty' /tmp/om-superset-pipeline-body)"
            elif [ "$superset_check_code" = "404" ]; then
              superset_create_code="$(curl -sS -o /tmp/om-superset-pipeline-body -w '%%{http_code}' \
                -X POST "$om_url/api/v1/services/ingestionPipelines" \
                -H "Authorization: Bearer $ADMIN_JWT" \
                -H "Content-Type: application/json" \
                -d "{
                  \"name\": \"superset-metadata-ingestion\",
                  \"displayName\": \"Superset Dashboard Metadata\",
                  \"pipelineType\": \"metadata\",
                  \"sourceConfig\": {
                    \"config\": {
                      \"type\": \"DashboardMetadata\",
                      \"includeDataModels\": true
                    }
                  },
                  \"airflowConfig\": {
                    \"startDate\": \"2025-01-01T00:00:00.000Z\",
                    \"retries\": 1,
                    \"pausePipeline\": true
                  },
                  \"service\": {
                    \"id\": \"$superset_svc_id\",
                    \"type\": \"dashboardService\"
                  }
                }")"
              case " 200 201 " in
                *" $superset_create_code "*) superset_pipeline_id="$(jq -r '.id // empty' /tmp/om-superset-pipeline-body)" ;;
                *)
                  echo "Failed to create Superset ingestion pipeline (HTTP $superset_create_code)" >&2
                  cat /tmp/om-superset-pipeline-body >&2
                  exit 1
                  ;;
              esac
            else
              echo "Failed to inspect Superset ingestion pipeline (HTTP $superset_check_code)" >&2
              exit 1
            fi

            curl -sS -o /tmp/om-superset-deploy-body -X POST \
              "$om_url/api/v1/services/ingestionPipelines/deploy/$superset_pipeline_id" \
              -H "Authorization: Bearer $ADMIN_JWT" || true
            curl -sS -o /tmp/om-superset-trigger-body -X POST \
              "$om_url/api/v1/services/ingestionPipelines/trigger/$superset_pipeline_id" \
              -H "Authorization: Bearer $ADMIN_JWT" || true
            echo "Superset dashboard service registered."

            # Create or reuse the Polaris metadata ingestion pipeline.
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
            name  = "SUPERSET_ADMIN_PASSWORD"
            value = var.superset_admin_password
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
