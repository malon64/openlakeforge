#!/usr/bin/env bash
# Load local runtime defaults from Terraform provider contracts when available.
#
# This file is intended to be sourced. It keeps local fallback values so tests
# and profile parsing still work before the stack is applied.

OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR="${OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR:-infra/terraform/environments/local}"
OPENLAKEFORGE_QUERY_SQLALCHEMY_URI_IS_USER_SET="${OPENLAKEFORGE_QUERY_SQLALCHEMY_URI+x}"

set_default_contract_env() {
  export OPENLAKEFORGE_STORAGE_LOGICAL_NAME="${OPENLAKEFORGE_STORAGE_LOGICAL_NAME:-lakehouse_storage}"
  export OPENLAKEFORGE_STORAGE_PROVIDER="${OPENLAKEFORGE_STORAGE_PROVIDER:-local}"
  export OPENLAKEFORGE_STORAGE_IMPLEMENTATION="${OPENLAKEFORGE_STORAGE_IMPLEMENTATION:-storage.s3_compatible.seaweedfs}"
  export OPENLAKEFORGE_STORAGE_TYPE="${OPENLAKEFORGE_STORAGE_TYPE:-s3}"
  export OPENLAKEFORGE_STORAGE_BRONZE_BUCKET="${OPENLAKEFORGE_STORAGE_BRONZE_BUCKET:-lakehouse-bronze}"
  export OPENLAKEFORGE_STORAGE_SILVER_BUCKET="${OPENLAKEFORGE_STORAGE_SILVER_BUCKET:-lakehouse-silver}"
  export OPENLAKEFORGE_STORAGE_GOLD_BUCKET="${OPENLAKEFORGE_STORAGE_GOLD_BUCKET:-lakehouse-gold}"
  export OPENLAKEFORGE_STORAGE_BUCKET="${OPENLAKEFORGE_STORAGE_BUCKET:-${OPENLAKEFORGE_STORAGE_BRONZE_BUCKET}}"
  export OPENLAKEFORGE_STORAGE_REGION="${OPENLAKEFORGE_STORAGE_REGION:-us-east-1}"
  export OPENLAKEFORGE_STORAGE_ENDPOINT="${OPENLAKEFORGE_STORAGE_ENDPOINT:-http://seaweedfs-s3:8333}"
  export OPENLAKEFORGE_STORAGE_VIRTUAL_HOST_ENDPOINT="${OPENLAKEFORGE_STORAGE_VIRTUAL_HOST_ENDPOINT:-http://lakehouse.svc.cluster.local:8333}"
  export OPENLAKEFORGE_STORAGE_PATH_STYLE_ACCESS="${OPENLAKEFORGE_STORAGE_PATH_STYLE_ACCESS:-true}"
  export OPENLAKEFORGE_STORAGE_SSL_MODE="${OPENLAKEFORGE_STORAGE_SSL_MODE:-disabled}"
  export OPENLAKEFORGE_STORAGE_CREDENTIALS_SECRET_NAME="${OPENLAKEFORGE_STORAGE_CREDENTIALS_SECRET_NAME:-seaweedfs-s3-creds}"
  export OPENLAKEFORGE_STORAGE_ACCESS_KEY_ID_KEY="${OPENLAKEFORGE_STORAGE_ACCESS_KEY_ID_KEY:-AWS_ACCESS_KEY_ID}"
  export OPENLAKEFORGE_STORAGE_SECRET_ACCESS_KEY_KEY="${OPENLAKEFORGE_STORAGE_SECRET_ACCESS_KEY_KEY:-AWS_SECRET_ACCESS_KEY}"
  export OPENLAKEFORGE_STORAGE_S3_SERVICE_NAME="${OPENLAKEFORGE_STORAGE_S3_SERVICE_NAME:-seaweedfs-s3}"
  export OPENLAKEFORGE_STORAGE_S3_SERVICE_PORT="${OPENLAKEFORGE_STORAGE_S3_SERVICE_PORT:-8333}"

  export OPENLAKEFORGE_CATALOG_LOGICAL_NAME="${OPENLAKEFORGE_CATALOG_LOGICAL_NAME:-iceberg_catalog}"
  export OPENLAKEFORGE_CATALOG_IMPLEMENTATION="${OPENLAKEFORGE_CATALOG_IMPLEMENTATION:-catalog.iceberg_rest.polaris}"
  export OPENLAKEFORGE_CATALOG_TYPE="${OPENLAKEFORGE_CATALOG_TYPE:-rest}"
  export OPENLAKEFORGE_CATALOG_PROVIDER="${OPENLAKEFORGE_CATALOG_PROVIDER:-polaris}"
  export OPENLAKEFORGE_CATALOG_NAME="${OPENLAKEFORGE_CATALOG_NAME:-lakehouse_dev}"
  export OPENLAKEFORGE_CATALOG_RUNTIME_PROFILE="${OPENLAKEFORGE_CATALOG_RUNTIME_PROFILE:-polaris-rest}"
  export OPENLAKEFORGE_CATALOG_REST_URI="${OPENLAKEFORGE_CATALOG_REST_URI:-http://polaris:8181/api/catalog}"
  export OPENLAKEFORGE_CATALOG_TOKEN_URI="${OPENLAKEFORGE_CATALOG_TOKEN_URI:-http://polaris:8181/api/catalog/v1/oauth/tokens}"
  export OPENLAKEFORGE_CATALOG_OAUTH_SCOPE="${OPENLAKEFORGE_CATALOG_OAUTH_SCOPE:-PRINCIPAL_ROLE:ALL}"
  export OPENLAKEFORGE_CATALOG_WAREHOUSE="${OPENLAKEFORGE_CATALOG_WAREHOUSE:-lakehouse_dev}"
  export OPENLAKEFORGE_CATALOG_GLUE_REGION="${OPENLAKEFORGE_CATALOG_GLUE_REGION:-}"
  export OPENLAKEFORGE_CATALOG_GLUE_CATALOG_ID="${OPENLAKEFORGE_CATALOG_GLUE_CATALOG_ID:-}"
  export OPENLAKEFORGE_CATALOG_GLUE_REST_URI="${OPENLAKEFORGE_CATALOG_GLUE_REST_URI:-}"
  export OPENLAKEFORGE_CATALOG_NAMESPACE_MODEL="${OPENLAKEFORGE_CATALOG_NAMESPACE_MODEL:-product-layer}"
  export OPENLAKEFORGE_CATALOG_NAMESPACES_JSON="${OPENLAKEFORGE_CATALOG_NAMESPACES_JSON:-[{\"name\":\"sales_order_revenue_silver\",\"location\":\"s3://lakehouse-silver/sales_order_revenue_silver/\"},{\"name\":\"sales_order_revenue_gold\",\"location\":\"s3://lakehouse-gold/sales_order_revenue_gold/\"},{\"name\":\"sales_customer_health_silver\",\"location\":\"s3://lakehouse-silver/sales_customer_health_silver/\"},{\"name\":\"sales_customer_health_gold\",\"location\":\"s3://lakehouse-gold/sales_customer_health_gold/\"},{\"name\":\"supply_chain_inventory_reliability_silver\",\"location\":\"s3://lakehouse-silver/supply_chain_inventory_reliability_silver/\"},{\"name\":\"supply_chain_inventory_reliability_gold\",\"location\":\"s3://lakehouse-gold/supply_chain_inventory_reliability_gold/\"}]}"
  export OPENLAKEFORGE_CATALOG_SILVER_NAMESPACES_JSON="${OPENLAKEFORGE_CATALOG_SILVER_NAMESPACES_JSON:-{\"sales_order_revenue\":\"sales_order_revenue_silver\",\"sales_customer_health\":\"sales_customer_health_silver\",\"supply_chain_inventory_reliability\":\"supply_chain_inventory_reliability_silver\"}}"
  export OPENLAKEFORGE_CATALOG_GOLD_NAMESPACES_JSON="${OPENLAKEFORGE_CATALOG_GOLD_NAMESPACES_JSON:-{\"sales_order_revenue\":\"sales_order_revenue_gold\",\"sales_customer_health\":\"sales_customer_health_gold\",\"supply_chain_inventory_reliability\":\"supply_chain_inventory_reliability_gold\"}}"
  export OPENLAKEFORGE_CATALOG_SILVER_NAMESPACE="${OPENLAKEFORGE_CATALOG_SILVER_NAMESPACE:-}"
  export OPENLAKEFORGE_CATALOG_GOLD_NAMESPACE="${OPENLAKEFORGE_CATALOG_GOLD_NAMESPACE:-}"
  export OPENLAKEFORGE_CATALOG_FLOE_CREDENTIALS_SECRET_NAME="${OPENLAKEFORGE_CATALOG_FLOE_CREDENTIALS_SECRET_NAME:-polaris-floe-creds}"
  export OPENLAKEFORGE_CATALOG_FLOE_CLIENT_ID_KEY="${OPENLAKEFORGE_CATALOG_FLOE_CLIENT_ID_KEY:-POLARIS_FLOE_CLIENT_ID}"
  export OPENLAKEFORGE_CATALOG_FLOE_CLIENT_SECRET_KEY="${OPENLAKEFORGE_CATALOG_FLOE_CLIENT_SECRET_KEY:-POLARIS_FLOE_CLIENT_SECRET}"
  export OPENLAKEFORGE_CATALOG_DBT_CREDENTIALS_SECRET_NAME="${OPENLAKEFORGE_CATALOG_DBT_CREDENTIALS_SECRET_NAME:-polaris-dbt-creds}"
  export OPENLAKEFORGE_CATALOG_DBT_CLIENT_ID_KEY="${OPENLAKEFORGE_CATALOG_DBT_CLIENT_ID_KEY:-POLARIS_DBT_CLIENT_ID}"
  export OPENLAKEFORGE_CATALOG_DBT_CLIENT_SECRET_KEY="${OPENLAKEFORGE_CATALOG_DBT_CLIENT_SECRET_KEY:-POLARIS_DBT_CLIENT_SECRET}"

  export OPENLAKEFORGE_OPS_BUCKET_NAME="${OPENLAKEFORGE_OPS_BUCKET_NAME:-${OPENLAKEFORGE_ARTIFACT_BUCKET_NAME:-openlakeforge-ops}}"
  export OPENLAKEFORGE_ARTIFACT_BUCKET_NAME="${OPENLAKEFORGE_ARTIFACT_BUCKET_NAME:-${OPENLAKEFORGE_OPS_BUCKET_NAME}}"
  export OPENLAKEFORGE_ARTIFACT_BASE_URI="${OPENLAKEFORGE_ARTIFACT_BASE_URI:-s3://${OPENLAKEFORGE_ARTIFACT_BUCKET_NAME}}"
  export OPENLAKEFORGE_FLOE_MANIFEST_ACCESS_MODE="${OPENLAKEFORGE_FLOE_MANIFEST_ACCESS_MODE:-remote}"
  export OPENLAKEFORGE_FLOE_MANIFEST_BASE_URI="${OPENLAKEFORGE_FLOE_MANIFEST_BASE_URI:-${OPENLAKEFORGE_ARTIFACT_BASE_URI}/floe/manifests}"
  export OPENLAKEFORGE_FLOE_REPORT_BASE_URI="${OPENLAKEFORGE_FLOE_REPORT_BASE_URI:-${OPENLAKEFORGE_ARTIFACT_BASE_URI}/floe/reports}"
  export OPENLAKEFORGE_LOG_BASE_URI="${OPENLAKEFORGE_LOG_BASE_URI:-${OPENLAKEFORGE_ARTIFACT_BASE_URI}/logs}"
  export OPENLAKEFORGE_RUN_ARTIFACT_BASE_URI="${OPENLAKEFORGE_RUN_ARTIFACT_BASE_URI:-${OPENLAKEFORGE_ARTIFACT_BASE_URI}/run-artifacts}"
  export OPENLAKEFORGE_ARTIFACT_LOCAL_UPLOAD_ACCESS_MODE="${OPENLAKEFORGE_ARTIFACT_LOCAL_UPLOAD_ACCESS_MODE:-kubectl-port-forward}"
  export OPENLAKEFORGE_QUERY_TRINO_HOST="${OPENLAKEFORGE_QUERY_TRINO_HOST:-trino}"
  export OPENLAKEFORGE_QUERY_TRINO_PORT="${OPENLAKEFORGE_QUERY_TRINO_PORT:-8080}"
  export OPENLAKEFORGE_QUERY_TRINO_CATALOG="${OPENLAKEFORGE_QUERY_TRINO_CATALOG:-iceberg}"

  export OPENLAKEFORGE_KUBE_NAMESPACE="${OPENLAKEFORGE_KUBE_NAMESPACE:-${NAMESPACE:-lakehouse}}"

  export AWS_REGION="${AWS_REGION:-${OPENLAKEFORGE_STORAGE_REGION}}"
  export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-${AWS_REGION}}"
  if [[ "${OPENLAKEFORGE_STORAGE_IMPLEMENTATION}" == "storage.aws_s3" ]]; then
    unset AWS_ENDPOINT_URL_S3
    unset OPENLAKEFORGE_DUCKDB_S3_ENDPOINT
    export AWS_S3_FORCE_PATH_STYLE="${AWS_S3_FORCE_PATH_STYLE:-false}"
  else
    export AWS_ENDPOINT_URL_S3="${AWS_ENDPOINT_URL_S3:-${OPENLAKEFORGE_STORAGE_ENDPOINT}}"
    export OPENLAKEFORGE_DUCKDB_S3_ENDPOINT="${OPENLAKEFORGE_DUCKDB_S3_ENDPOINT:-${OPENLAKEFORGE_STORAGE_ENDPOINT#http://}}"
    export AWS_S3_FORCE_PATH_STYLE="${AWS_S3_FORCE_PATH_STYLE:-${OPENLAKEFORGE_STORAGE_PATH_STYLE_ACCESS}}"
  fi
  if [[ "${OPENLAKEFORGE_STORAGE_SSL_MODE}" == "disabled" ]]; then
    export AWS_ALLOW_HTTP="${AWS_ALLOW_HTTP:-true}"
  fi

  # Compatibility names for current Polaris REST consumers.
  export POLARIS_REST_URI="${POLARIS_REST_URI:-${OPENLAKEFORGE_CATALOG_REST_URI}}"
  export POLARIS_TOKEN_URI="${POLARIS_TOKEN_URI:-${OPENLAKEFORGE_CATALOG_TOKEN_URI}}"
  export POLARIS_WAREHOUSE="${POLARIS_WAREHOUSE:-${OPENLAKEFORGE_CATALOG_WAREHOUSE}}"
  export POLARIS_OAUTH_SCOPE="${POLARIS_OAUTH_SCOPE:-${OPENLAKEFORGE_CATALOG_OAUTH_SCOPE}}"
  export OPENLAKEFORGE_CATALOG_DBT_CLIENT_ID="${OPENLAKEFORGE_CATALOG_DBT_CLIENT_ID:-${POLARIS_DBT_CLIENT_ID:-openlakeforge-dbt}}"
  export OPENLAKEFORGE_CATALOG_DBT_CLIENT_SECRET="${OPENLAKEFORGE_CATALOG_DBT_CLIENT_SECRET:-${POLARIS_DBT_CLIENT_SECRET:-openlakeforge-dbt}}"
}

set_default_contract_env

if command -v terraform >/dev/null 2>&1 &&
  command -v python3 >/dev/null 2>&1 &&
  [[ -d "${OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR}" ]] &&
  terraform -chdir="${OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR}" output -json provider_contracts >/tmp/openlakeforge-provider-contracts.json 2>/dev/null; then
  eval "$(
    python3 - /tmp/openlakeforge-provider-contracts.json <<'PY'
import json
import shlex
import sys
from pathlib import Path

contracts = json.loads(Path(sys.argv[1]).read_text())
storage = contracts.get("storage", {})
catalog = contracts.get("catalog", {})
artifact_bucket = contracts.get("artifact_bucket", contracts.get("artifacts", {}))
platform = contracts.get("kubernetes_platform", contracts.get("cluster", {}))
query = contracts.get("query", {})


def emit(name, value):
    if value is None:
        return
    if isinstance(value, bool):
        value = "true" if value else "false"
    print(f"export {name}={shlex.quote(str(value))}")


def emit_json(name, value):
    if value is None:
        return
    print(f"export {name}={shlex.quote(json.dumps(value, separators=(',', ':')))}")


emit("OPENLAKEFORGE_STORAGE_LOGICAL_NAME", storage.get("logical_name"))
emit("OPENLAKEFORGE_STORAGE_PROVIDER", storage.get("provider"))
emit("OPENLAKEFORGE_STORAGE_IMPLEMENTATION", storage.get("implementation"))
emit("OPENLAKEFORGE_STORAGE_TYPE", storage.get("protocol"))
emit("OPENLAKEFORGE_STORAGE_BRONZE_BUCKET", storage.get("bronze_bucket_name"))
emit("OPENLAKEFORGE_STORAGE_SILVER_BUCKET", storage.get("silver_bucket_name"))
emit("OPENLAKEFORGE_STORAGE_GOLD_BUCKET", storage.get("gold_bucket_name"))
emit("OPENLAKEFORGE_STORAGE_BUCKET", storage.get("bucket_name"))
emit("OPENLAKEFORGE_STORAGE_REGION", storage.get("region"))
emit("OPENLAKEFORGE_STORAGE_ENDPOINT", storage.get("endpoint"))
emit("OPENLAKEFORGE_STORAGE_VIRTUAL_HOST_ENDPOINT", storage.get("virtual_host_endpoint"))
emit("OPENLAKEFORGE_STORAGE_PATH_STYLE_ACCESS", storage.get("path_style_access"))
emit("OPENLAKEFORGE_STORAGE_SSL_MODE", storage.get("ssl_mode"))
emit("OPENLAKEFORGE_STORAGE_CREDENTIALS_SECRET_NAME", storage.get("credentials_secret_name"))
emit("OPENLAKEFORGE_STORAGE_ACCESS_KEY_ID_KEY", storage.get("access_key_id_key"))
emit("OPENLAKEFORGE_STORAGE_SECRET_ACCESS_KEY_KEY", storage.get("secret_access_key_key"))
emit("OPENLAKEFORGE_STORAGE_S3_SERVICE_NAME", storage.get("s3_service_name"))
emit("OPENLAKEFORGE_STORAGE_S3_SERVICE_PORT", storage.get("s3_service_port"))

emit("OPENLAKEFORGE_CATALOG_LOGICAL_NAME", catalog.get("logical_name"))
emit("OPENLAKEFORGE_CATALOG_IMPLEMENTATION", catalog.get("implementation"))
emit("OPENLAKEFORGE_CATALOG_TYPE", catalog.get("catalog_type"))
emit("OPENLAKEFORGE_CATALOG_PROVIDER", catalog.get("catalog_provider"))
emit("OPENLAKEFORGE_CATALOG_NAME", catalog.get("catalog_name"))
emit("OPENLAKEFORGE_CATALOG_RUNTIME_PROFILE", catalog.get("runtime_profile"))
emit("OPENLAKEFORGE_CATALOG_REST_URI", catalog.get("rest_uri"))
emit("OPENLAKEFORGE_CATALOG_TOKEN_URI", catalog.get("token_uri"))
emit("OPENLAKEFORGE_CATALOG_OAUTH_SCOPE", catalog.get("oauth_scope"))
emit("OPENLAKEFORGE_CATALOG_WAREHOUSE", catalog.get("warehouse") or catalog.get("catalog_name"))
emit("OPENLAKEFORGE_CATALOG_GLUE_REGION", catalog.get("glue_region"))
emit("OPENLAKEFORGE_CATALOG_GLUE_CATALOG_ID", catalog.get("glue_catalog_id"))
emit("OPENLAKEFORGE_CATALOG_GLUE_REST_URI", catalog.get("glue_rest_uri") or catalog.get("rest_uri"))
emit("OPENLAKEFORGE_CATALOG_NAMESPACE_MODEL", catalog.get("catalog_namespace_model"))
emit_json("OPENLAKEFORGE_CATALOG_NAMESPACES_JSON", catalog.get("catalog_namespaces"))
emit_json("OPENLAKEFORGE_CATALOG_SILVER_NAMESPACES_JSON", catalog.get("silver_namespaces"))
emit_json("OPENLAKEFORGE_CATALOG_GOLD_NAMESPACES_JSON", catalog.get("gold_namespaces"))
emit("OPENLAKEFORGE_CATALOG_SILVER_NAMESPACE", catalog.get("silver_namespace"))
emit("OPENLAKEFORGE_CATALOG_GOLD_NAMESPACE", catalog.get("gold_namespace"))
emit("OPENLAKEFORGE_CATALOG_FLOE_CREDENTIALS_SECRET_NAME", catalog.get("floe_credentials_secret_name"))
emit("OPENLAKEFORGE_CATALOG_FLOE_CLIENT_ID_KEY", catalog.get("floe_client_id_key"))
emit("OPENLAKEFORGE_CATALOG_FLOE_CLIENT_SECRET_KEY", catalog.get("floe_client_secret_key"))
emit("OPENLAKEFORGE_CATALOG_DBT_CREDENTIALS_SECRET_NAME", catalog.get("dbt_credentials_secret_name"))
emit("OPENLAKEFORGE_CATALOG_DBT_CLIENT_ID_KEY", catalog.get("dbt_client_id_key"))
emit("OPENLAKEFORGE_CATALOG_DBT_CLIENT_SECRET_KEY", catalog.get("dbt_client_secret_key"))

emit("OPENLAKEFORGE_OPS_BUCKET_NAME", artifact_bucket.get("bucket_name") or artifact_bucket.get("ops_bucket_name"))
emit("OPENLAKEFORGE_ARTIFACT_BUCKET_NAME", artifact_bucket.get("bucket_name") or artifact_bucket.get("ops_bucket_name"))
emit("OPENLAKEFORGE_ARTIFACT_BASE_URI", artifact_bucket.get("artifact_base_uri"))
emit("OPENLAKEFORGE_FLOE_MANIFEST_ACCESS_MODE", artifact_bucket.get("access_mode") or artifact_bucket.get("floe_manifest_access_mode"))
emit("OPENLAKEFORGE_FLOE_MANIFEST_BASE_URI", artifact_bucket.get("base_uri") or artifact_bucket.get("floe_manifest_base_uri"))
emit("OPENLAKEFORGE_FLOE_REPORT_BASE_URI", artifact_bucket.get("floe_report_base_uri"))
emit("OPENLAKEFORGE_LOG_BASE_URI", artifact_bucket.get("log_base_uri"))
emit("OPENLAKEFORGE_RUN_ARTIFACT_BASE_URI", artifact_bucket.get("run_artifact_base_uri"))
emit("OPENLAKEFORGE_ARTIFACT_LOCAL_UPLOAD_ACCESS_MODE", artifact_bucket.get("local_upload_access_mode"))
emit("OPENLAKEFORGE_KUBE_NAMESPACE", platform.get("namespace"))
emit("OPENLAKEFORGE_QUERY_TRINO_CATALOG", query.get("catalog_name"))
endpoint = query.get("endpoint")
if endpoint and endpoint.startswith("http://"):
    host_port = endpoint.removeprefix("http://").split("/", 1)[0]
    if ":" in host_port:
        host, port = host_port.rsplit(":", 1)
        emit("OPENLAKEFORGE_QUERY_TRINO_HOST", host)
        emit("OPENLAKEFORGE_QUERY_TRINO_PORT", port)
PY
  )"

  set_default_contract_env
fi

if [[ "${OPENLAKEFORGE_STORAGE_IMPLEMENTATION}" == "storage.aws_s3" ]]; then
  export OPENLAKEFORGE_STORAGE_ENDPOINT=""
  export OPENLAKEFORGE_STORAGE_VIRTUAL_HOST_ENDPOINT=""
  export OPENLAKEFORGE_STORAGE_PATH_STYLE_ACCESS="false"
  export OPENLAKEFORGE_STORAGE_CREDENTIALS_SECRET_NAME=""
  export OPENLAKEFORGE_STORAGE_ACCESS_KEY_ID_KEY=""
  export OPENLAKEFORGE_STORAGE_SECRET_ACCESS_KEY_KEY=""
  export OPENLAKEFORGE_STORAGE_S3_SERVICE_NAME=""
  export OPENLAKEFORGE_STORAGE_S3_SERVICE_PORT=""
  unset AWS_ENDPOINT_URL_S3
  unset OPENLAKEFORGE_DUCKDB_S3_ENDPOINT
  export AWS_S3_FORCE_PATH_STYLE="${AWS_S3_FORCE_PATH_STYLE:-false}"
fi
if [[ "${OPENLAKEFORGE_CATALOG_TYPE}" == "glue" && "${OPENLAKEFORGE_CATALOG_PROVIDER}" == "aws-glue" ]]; then
  export OPENLAKEFORGE_CATALOG_TOKEN_URI=""
  export OPENLAKEFORGE_CATALOG_OAUTH_SCOPE=""
  export OPENLAKEFORGE_CATALOG_FLOE_CREDENTIALS_SECRET_NAME=""
  export OPENLAKEFORGE_CATALOG_FLOE_CLIENT_ID_KEY=""
  export OPENLAKEFORGE_CATALOG_FLOE_CLIENT_SECRET_KEY=""
  export OPENLAKEFORGE_CATALOG_DBT_CREDENTIALS_SECRET_NAME=""
  export OPENLAKEFORGE_CATALOG_DBT_CLIENT_ID_KEY=""
  export OPENLAKEFORGE_CATALOG_DBT_CLIENT_SECRET_KEY=""
fi

export CODE_BUCKET_NAME="${CODE_BUCKET_NAME:-${OPENLAKEFORGE_ARTIFACT_BUCKET_NAME}}"
export OPENMETADATA_CATALOG_SERVICE="${OPENMETADATA_CATALOG_SERVICE:-${OPENLAKEFORGE_CATALOG_PROVIDER}}"
export OPENMETADATA_CATALOG_DATABASE="${OPENMETADATA_CATALOG_DATABASE:-${OPENLAKEFORGE_CATALOG_NAME}}"
if [[ -z "${OPENLAKEFORGE_QUERY_SQLALCHEMY_URI_IS_USER_SET}" ]]; then
  export OPENLAKEFORGE_QUERY_SQLALCHEMY_URI="trino://superset@${OPENLAKEFORGE_QUERY_TRINO_HOST}:${OPENLAKEFORGE_QUERY_TRINO_PORT}/${OPENLAKEFORGE_QUERY_TRINO_CATALOG}"
fi
