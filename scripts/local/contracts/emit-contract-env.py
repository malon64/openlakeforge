#!/usr/bin/env python3
"""Emit `export NAME=value` lines from a Terraform provider_contracts JSON.

Shared by every provider's runtime-env loader (local, AWS, Azure) via
load-runtime-env.sh. Kept as a standalone file rather than an inline heredoc so
the same logic is not duplicated per provider and so it does not depend on the
shell correctly parsing a heredoc nested inside a command substitution (macOS's
bash 3.2 mis-parses that form).

Usage: emit-contract-env.py <provider-contracts.json>
"""
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
is_glue_catalog = catalog.get("catalog_type") == "glue" and catalog.get("catalog_provider") == "aws-glue"
catalog_warehouse = (
    catalog.get("catalog_name")
    if is_glue_catalog
    else (catalog.get("warehouse") or catalog.get("catalog_name"))
)


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
emit("OPENLAKEFORGE_CATALOG_WAREHOUSE", catalog_warehouse)
emit("OPENLAKEFORGE_CATALOG_GLUE_REGION", catalog.get("glue_region"))
emit("OPENLAKEFORGE_CATALOG_GLUE_CATALOG_ID", catalog.get("glue_catalog_id"))
emit("OPENLAKEFORGE_CATALOG_GLUE_REST_URI", catalog.get("glue_rest_uri") or catalog.get("rest_uri"))
emit("OPENLAKEFORGE_CATALOG_GLUE_REST_WAREHOUSE", catalog.get("glue_rest_warehouse") or catalog.get("warehouse"))
emit("OPENLAKEFORGE_CATALOG_GLUE_DATABASE", catalog.get("glue_database"))
emit("OPENLAKEFORGE_CATALOG_GLUE_WAREHOUSE_PREFIX", catalog.get("glue_warehouse_prefix"))
emit("OPENLAKEFORGE_CATALOG_NAMESPACE_MODEL", catalog.get("catalog_namespace_model"))
emit_json("OPENLAKEFORGE_CATALOG_NAMESPACES_JSON", catalog.get("catalog_namespaces"))
emit_json("OPENLAKEFORGE_CATALOG_SILVER_NAMESPACES_JSON", catalog.get("silver_namespaces"))
emit_json("OPENLAKEFORGE_CATALOG_GOLD_NAMESPACES_JSON", catalog.get("gold_namespaces"))
emit("OPENLAKEFORGE_CATALOG_DATABASE_FQN", catalog.get("catalog_database_fqn"))
emit_json("OPENLAKEFORGE_CATALOG_SILVER_SCHEMA_FQNS_JSON", catalog.get("silver_schema_fqns"))
emit_json("OPENLAKEFORGE_CATALOG_GOLD_SCHEMA_FQNS_JSON", catalog.get("gold_schema_fqns"))
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
