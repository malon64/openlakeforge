"""Provider-contract runtime environment resolution.

Single implementation of the logic that used to live in
scripts/local/contracts/load-runtime-env.sh (fallback defaults + provider
branching) and scripts/local/contracts/emit-contract-env.py (Terraform
`provider_contracts` output normalization). The shell wrapper evaluates the
`export`/`unset` lines this module prints, so exported variable names and
values must stay byte-identical for every provider.

Semantics preserved from the shell implementation:
- Defaults apply where a variable is unset OR empty (bash ``${VAR:-default}``).
- Terraform contract values override inherited environment values.
- After contract application, defaults run again to fill remaining gaps.
- Four variables honor a caller-set value even when contracts disagree:
  OPENLAKEFORGE_QUERY_SQLALCHEMY_URI, OPENMETADATA_CATALOG_SERVICE,
  OPENLAKEFORGE_STORAGE_OM_SERVICE, OPENLAKEFORGE_STORAGE_DISPLAY_NAME
  (bash ``${VAR+x}`` set-ness, which includes empty strings).
"""

from __future__ import annotations

import json
import shlex
import subprocess
from collections.abc import Mapping
from typing import Any

# Kept as literal compact-JSON strings for byte parity with the previous bash
# defaults.
_DEFAULT_CATALOG_NAMESPACES_JSON = (
    '[{"name":"sales_order_revenue_silver","location":"s3://lakehouse-silver/'
    'sales_order_revenue_silver/"},{"name":"sales_order_revenue_gold","location":'
    '"s3://lakehouse-gold/sales_order_revenue_gold/"},{"name":"sales_customer_health_silver",'
    '"location":"s3://lakehouse-silver/sales_customer_health_silver/"},'
    '{"name":"sales_customer_health_gold","location":"s3://lakehouse-gold/'
    'sales_customer_health_gold/"},{"name":"supply_chain_inventory_reliability_silver",'
    '"location":"s3://lakehouse-silver/supply_chain_inventory_reliability_silver/"},'
    '{"name":"supply_chain_inventory_reliability_gold","location":"s3://lakehouse-gold/'
    'supply_chain_inventory_reliability_gold/"}]'
)
_DEFAULT_CATALOG_SILVER_NAMESPACES_JSON = (
    '{"sales_order_revenue":"sales_order_revenue_silver",'
    '"sales_customer_health":"sales_customer_health_silver",'
    '"supply_chain_inventory_reliability":"supply_chain_inventory_reliability_silver"}'
)
_DEFAULT_CATALOG_GOLD_NAMESPACES_JSON = (
    '{"sales_order_revenue":"sales_order_revenue_gold",'
    '"sales_customer_health":"sales_customer_health_gold",'
    '"supply_chain_inventory_reliability":"supply_chain_inventory_reliability_gold"}'
)

_PRODUCTS = ("sales_order_revenue", "sales_customer_health", "supply_chain_inventory_reliability")


def load_provider_contracts(terraform_dir: str) -> dict[str, Any] | None:
    """Read the Terraform provider_contracts output, or None before apply."""
    try:
        result = subprocess.run(
            ["terraform", f"-chdir={terraform_dir}", "output", "-json", "provider_contracts"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    try:
        contracts = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return contracts if isinstance(contracts, dict) else None


class _Env:
    """Mutable environment view that records exports and unsets."""

    def __init__(self, base: Mapping[str, str]):
        self._values = dict(base)
        self.exports: dict[str, str] = {}
        self.unsets: list[str] = []

    def get(self, name: str, fallback: str = "") -> str:
        value = self._values.get(name)
        return fallback if value is None or value == "" else value

    def raw(self, name: str) -> str | None:
        return self._values.get(name)

    def set(self, name: str, value: str) -> None:
        self._values[name] = value
        self.exports[name] = value
        if name in self.unsets:
            self.unsets.remove(name)

    def default(self, name: str, value: str) -> None:
        # bash ${NAME:-value}: unset or empty selects the default. The bash
        # scripts also re-export the existing value, which is a no-op for the
        # eval'ing shell, so only record a change when the default applies.
        current = self._values.get(name)
        if current is None or current == "":
            self.set(name, value)
        elif name not in self.exports:
            self.exports[name] = current

    def unset(self, name: str) -> None:
        self._values.pop(name, None)
        self.exports.pop(name, None)
        if name not in self.unsets:
            self.unsets.append(name)


def _contract_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _apply_default_contract_env(env: _Env, base: Mapping[str, str]) -> None:
    env.default("OPENLAKEFORGE_STORAGE_LOGICAL_NAME", "lakehouse_storage")
    env.default("OPENLAKEFORGE_STORAGE_PROVIDER", "local")
    env.default("OPENLAKEFORGE_STORAGE_IMPLEMENTATION", "storage.s3_compatible.seaweedfs")
    env.default("OPENLAKEFORGE_STORAGE_TYPE", "s3")
    env.default("OPENLAKEFORGE_STORAGE_BRONZE_BUCKET", "lakehouse-bronze")
    env.default("OPENLAKEFORGE_STORAGE_SILVER_BUCKET", "lakehouse-silver")
    env.default("OPENLAKEFORGE_STORAGE_GOLD_BUCKET", "lakehouse-gold")
    env.default("OPENLAKEFORGE_STORAGE_BUCKET", env.get("OPENLAKEFORGE_STORAGE_BRONZE_BUCKET"))
    env.default("OPENLAKEFORGE_STORAGE_REGION", "us-east-1")
    env.default("OPENLAKEFORGE_STORAGE_ENDPOINT", "http://seaweedfs-s3:8333")
    env.default(
        "OPENLAKEFORGE_STORAGE_VIRTUAL_HOST_ENDPOINT", "http://lakehouse.svc.cluster.local:8333"
    )
    env.default("OPENLAKEFORGE_STORAGE_PATH_STYLE_ACCESS", "true")
    env.default("OPENLAKEFORGE_STORAGE_SSL_MODE", "disabled")
    env.default("OPENLAKEFORGE_STORAGE_CREDENTIALS_SECRET_NAME", "seaweedfs-s3-creds")
    env.default("OPENLAKEFORGE_STORAGE_ACCESS_KEY_ID_KEY", "AWS_ACCESS_KEY_ID")
    env.default("OPENLAKEFORGE_STORAGE_SECRET_ACCESS_KEY_KEY", "AWS_SECRET_ACCESS_KEY")
    env.default("OPENLAKEFORGE_STORAGE_S3_SERVICE_NAME", "seaweedfs-s3")
    env.default("OPENLAKEFORGE_STORAGE_S3_SERVICE_PORT", "8333")

    env.default("OPENLAKEFORGE_CATALOG_LOGICAL_NAME", "iceberg_catalog")
    env.default("OPENLAKEFORGE_CATALOG_IMPLEMENTATION", "catalog.iceberg_rest.polaris")
    env.default("OPENLAKEFORGE_CATALOG_TYPE", "rest")
    env.default("OPENLAKEFORGE_CATALOG_PROVIDER", "polaris")
    env.default("OPENLAKEFORGE_CATALOG_NAME", "lakehouse_dev")
    env.default("OPENLAKEFORGE_CATALOG_RUNTIME_PROFILE", "polaris-rest")
    env.default("OPENLAKEFORGE_CATALOG_REST_URI", "http://polaris:8181/api/catalog")
    env.default("OPENLAKEFORGE_CATALOG_TOKEN_URI", "http://polaris:8181/api/catalog/v1/oauth/tokens")
    env.default("OPENLAKEFORGE_CATALOG_OAUTH_SCOPE", "PRINCIPAL_ROLE:ALL")
    env.default("OPENLAKEFORGE_CATALOG_WAREHOUSE", "lakehouse_dev")
    env.default("OPENLAKEFORGE_CATALOG_GLUE_REGION", "")
    env.default("OPENLAKEFORGE_CATALOG_GLUE_CATALOG_ID", "")
    env.default("OPENLAKEFORGE_CATALOG_GLUE_REST_URI", "")
    env.default(
        "OPENLAKEFORGE_CATALOG_GLUE_REST_WAREHOUSE", env.get("OPENLAKEFORGE_CATALOG_GLUE_CATALOG_ID")
    )
    env.default("OPENLAKEFORGE_CATALOG_GLUE_DATABASE", "")
    env.default("OPENLAKEFORGE_CATALOG_GLUE_WAREHOUSE_PREFIX", "warehouse/iceberg")
    env.default("OPENLAKEFORGE_CATALOG_NAMESPACE_MODEL", "product-layer")
    env.default("OPENLAKEFORGE_CATALOG_NAMESPACES_JSON", _DEFAULT_CATALOG_NAMESPACES_JSON)
    env.default(
        "OPENLAKEFORGE_CATALOG_SILVER_NAMESPACES_JSON", _DEFAULT_CATALOG_SILVER_NAMESPACES_JSON
    )
    env.default("OPENLAKEFORGE_CATALOG_GOLD_NAMESPACES_JSON", _DEFAULT_CATALOG_GOLD_NAMESPACES_JSON)
    env.default("OPENLAKEFORGE_CATALOG_SILVER_NAMESPACE", "")
    env.default("OPENLAKEFORGE_CATALOG_GOLD_NAMESPACE", "")
    env.default("OPENLAKEFORGE_CATALOG_FLOE_CREDENTIALS_SECRET_NAME", "polaris-floe-creds")
    env.default("OPENLAKEFORGE_CATALOG_FLOE_CLIENT_ID_KEY", "POLARIS_FLOE_CLIENT_ID")
    env.default("OPENLAKEFORGE_CATALOG_FLOE_CLIENT_SECRET_KEY", "POLARIS_FLOE_CLIENT_SECRET")

    env.default(
        "OPENLAKEFORGE_OPS_BUCKET_NAME",
        env.get("OPENLAKEFORGE_ARTIFACT_BUCKET_NAME", "openlakeforge-ops"),
    )
    env.default(
        "OPENLAKEFORGE_ARTIFACT_BUCKET_NAME", env.get("OPENLAKEFORGE_OPS_BUCKET_NAME")
    )
    env.default(
        "OPENLAKEFORGE_ARTIFACT_BASE_URI",
        f"s3://{env.get('OPENLAKEFORGE_ARTIFACT_BUCKET_NAME')}",
    )
    env.default("OPENLAKEFORGE_FLOE_MANIFEST_ACCESS_MODE", "remote")
    env.default(
        "OPENLAKEFORGE_FLOE_MANIFEST_BASE_URI",
        f"{env.get('OPENLAKEFORGE_ARTIFACT_BASE_URI')}/floe/manifests",
    )
    env.default(
        "OPENLAKEFORGE_FLOE_REPORT_BASE_URI",
        f"{env.get('OPENLAKEFORGE_ARTIFACT_BASE_URI')}/floe/reports",
    )
    env.default(
        "OPENLAKEFORGE_LOG_BASE_URI", f"{env.get('OPENLAKEFORGE_ARTIFACT_BASE_URI')}/logs"
    )
    env.default(
        "OPENLAKEFORGE_RUN_ARTIFACT_BASE_URI",
        f"{env.get('OPENLAKEFORGE_ARTIFACT_BASE_URI')}/run-artifacts",
    )
    env.default("OPENLAKEFORGE_ARTIFACT_LOCAL_UPLOAD_ACCESS_MODE", "kubectl-port-forward")
    env.default("OPENLAKEFORGE_QUERY_TRINO_HOST", "trino")
    env.default("OPENLAKEFORGE_QUERY_TRINO_PORT", "8080")
    env.default("OPENLAKEFORGE_QUERY_TRINO_CATALOG", "iceberg")

    env.default("OPENLAKEFORGE_KUBE_NAMESPACE", env.get("NAMESPACE", "lakehouse"))

    env.default("AWS_REGION", env.get("OPENLAKEFORGE_STORAGE_REGION"))
    env.default("AWS_DEFAULT_REGION", env.get("AWS_REGION"))
    if env.get("OPENLAKEFORGE_STORAGE_IMPLEMENTATION") == "storage.aws_s3":
        env.unset("AWS_ENDPOINT_URL_S3")
        env.default("AWS_S3_FORCE_PATH_STYLE", "false")
    else:
        env.default("AWS_ENDPOINT_URL_S3", env.get("OPENLAKEFORGE_STORAGE_ENDPOINT"))
        env.default("AWS_S3_FORCE_PATH_STYLE", env.get("OPENLAKEFORGE_STORAGE_PATH_STYLE_ACCESS"))
    if env.get("OPENLAKEFORGE_STORAGE_SSL_MODE") == "disabled":
        env.default("AWS_ALLOW_HTTP", "true")

    # Compatibility names for current Polaris REST consumers.
    env.default("POLARIS_REST_URI", env.get("OPENLAKEFORGE_CATALOG_REST_URI"))
    env.default("POLARIS_TOKEN_URI", env.get("OPENLAKEFORGE_CATALOG_TOKEN_URI"))
    env.default("POLARIS_WAREHOUSE", env.get("OPENLAKEFORGE_CATALOG_WAREHOUSE"))
    env.default("POLARIS_OAUTH_SCOPE", env.get("OPENLAKEFORGE_CATALOG_OAUTH_SCOPE"))
    env.default("OPENLAKEFORGE_DBT_TRINO_USER", "openlakeforge-dbt")
    env.default("OPENLAKEFORGE_DBT_EXECUTABLE", "dbt-ol")
    env.default("OPENLINEAGE_URL", "http://openmetadata:8585")
    env.default("OPENLINEAGE_ENDPOINT", "api/v1/openlineage/lineage")
    env.default("OPENLINEAGE_NAMESPACE", "dagster")


def _apply_provider_contracts(env: _Env, contracts: dict[str, Any]) -> None:
    storage = contracts.get("storage") or {}
    catalog = contracts.get("catalog") or {}
    artifact_bucket = contracts.get("artifact_bucket", contracts.get("artifacts")) or {}
    platform = contracts.get("kubernetes_platform", contracts.get("cluster")) or {}
    query = contracts.get("query") or {}
    is_glue_catalog = (
        catalog.get("catalog_type") == "glue" and catalog.get("catalog_provider") == "aws-glue"
    )
    catalog_warehouse = (
        catalog.get("catalog_name")
        if is_glue_catalog
        else (catalog.get("warehouse") or catalog.get("catalog_name"))
    )

    def emit(name: str, value: Any) -> None:
        if value is None:
            return
        env.set(name, _contract_value(value))

    def emit_json(name: str, value: Any) -> None:
        if value is None:
            return
        env.set(name, json.dumps(value, separators=(",", ":")))

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
    emit(
        "OPENLAKEFORGE_CATALOG_GLUE_REST_WAREHOUSE",
        catalog.get("glue_rest_warehouse") or catalog.get("warehouse"),
    )
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
    emit(
        "OPENLAKEFORGE_CATALOG_FLOE_CREDENTIALS_SECRET_NAME",
        catalog.get("floe_credentials_secret_name"),
    )
    emit("OPENLAKEFORGE_CATALOG_FLOE_CLIENT_ID_KEY", catalog.get("floe_client_id_key"))
    emit("OPENLAKEFORGE_CATALOG_FLOE_CLIENT_SECRET_KEY", catalog.get("floe_client_secret_key"))

    ops_bucket = artifact_bucket.get("bucket_name") or artifact_bucket.get("ops_bucket_name")
    emit("OPENLAKEFORGE_OPS_BUCKET_NAME", ops_bucket)
    emit("OPENLAKEFORGE_ARTIFACT_BUCKET_NAME", ops_bucket)
    emit("OPENLAKEFORGE_ARTIFACT_BASE_URI", artifact_bucket.get("artifact_base_uri"))
    emit(
        "OPENLAKEFORGE_FLOE_MANIFEST_ACCESS_MODE",
        artifact_bucket.get("access_mode") or artifact_bucket.get("floe_manifest_access_mode"),
    )
    emit(
        "OPENLAKEFORGE_FLOE_MANIFEST_BASE_URI",
        artifact_bucket.get("base_uri") or artifact_bucket.get("floe_manifest_base_uri"),
    )
    emit("OPENLAKEFORGE_FLOE_REPORT_BASE_URI", artifact_bucket.get("floe_report_base_uri"))
    emit("OPENLAKEFORGE_LOG_BASE_URI", artifact_bucket.get("log_base_uri"))
    emit("OPENLAKEFORGE_RUN_ARTIFACT_BASE_URI", artifact_bucket.get("run_artifact_base_uri"))
    emit(
        "OPENLAKEFORGE_ARTIFACT_LOCAL_UPLOAD_ACCESS_MODE",
        artifact_bucket.get("local_upload_access_mode"),
    )
    emit("OPENLAKEFORGE_KUBE_NAMESPACE", platform.get("namespace"))
    emit("OPENLAKEFORGE_QUERY_TRINO_CATALOG", query.get("catalog_name"))
    endpoint = query.get("endpoint")
    if endpoint and endpoint.startswith("http://"):
        host_port = endpoint.removeprefix("http://").split("/", 1)[0]
        if ":" in host_port:
            host, port = host_port.rsplit(":", 1)
            emit("OPENLAKEFORGE_QUERY_TRINO_HOST", host)
            emit("OPENLAKEFORGE_QUERY_TRINO_PORT", port)


def build_contract_env(
    base: Mapping[str, str], contracts: dict[str, Any] | None
) -> tuple[dict[str, str], list[str]]:
    """Compute the runtime contract environment.

    Returns (exports, unsets): the variables to export (insertion-ordered)
    and the variables to unset in the caller's shell.
    """
    query_uri_user_set = "OPENLAKEFORGE_QUERY_SQLALCHEMY_URI" in base
    om_catalog_service_user_set = "OPENMETADATA_CATALOG_SERVICE" in base
    storage_om_service_user_set = "OPENLAKEFORGE_STORAGE_OM_SERVICE" in base
    storage_display_name_user_set = "OPENLAKEFORGE_STORAGE_DISPLAY_NAME" in base
    aws_region_user_set = "AWS_REGION" in base
    aws_default_region_user_set = "AWS_DEFAULT_REGION" in base

    env = _Env(base)
    _apply_default_contract_env(env, base)
    if contracts is not None:
        _apply_provider_contracts(env, contracts)
        _apply_default_contract_env(env, base)

    if env.get("OPENLAKEFORGE_STORAGE_IMPLEMENTATION") == "storage.aws_s3":
        env.set("OPENLAKEFORGE_STORAGE_ENDPOINT", "")
        env.set("OPENLAKEFORGE_STORAGE_VIRTUAL_HOST_ENDPOINT", "")
        env.set("OPENLAKEFORGE_STORAGE_PATH_STYLE_ACCESS", "false")
        env.set("OPENLAKEFORGE_STORAGE_CREDENTIALS_SECRET_NAME", "")
        env.set("OPENLAKEFORGE_STORAGE_ACCESS_KEY_ID_KEY", "")
        env.set("OPENLAKEFORGE_STORAGE_SECRET_ACCESS_KEY_KEY", "")
        env.set("OPENLAKEFORGE_STORAGE_S3_SERVICE_NAME", "")
        env.set("OPENLAKEFORGE_STORAGE_S3_SERVICE_PORT", "")
        env.unset("AWS_ENDPOINT_URL_S3")
        env.default("AWS_S3_FORCE_PATH_STYLE", "false")

    is_glue = (
        env.get("OPENLAKEFORGE_CATALOG_TYPE") == "glue"
        and env.get("OPENLAKEFORGE_CATALOG_PROVIDER") == "aws-glue"
    )
    if is_glue:
        env.default("OPENLAKEFORGE_CATALOG_GLUE_DATABASE", "")
        env.set("OPENLAKEFORGE_CATALOG_WAREHOUSE", env.get("OPENLAKEFORGE_CATALOG_NAME"))
        env.default(
            "OPENLAKEFORGE_CATALOG_GLUE_REST_WAREHOUSE",
            env.get("OPENLAKEFORGE_CATALOG_GLUE_CATALOG_ID"),
        )
        env.default("OPENLAKEFORGE_CATALOG_GLUE_WAREHOUSE_PREFIX", "warehouse/iceberg")
        env.set("OPENLAKEFORGE_CATALOG_TOKEN_URI", "")
        env.set("OPENLAKEFORGE_CATALOG_OAUTH_SCOPE", "")
        env.set("OPENLAKEFORGE_CATALOG_FLOE_CREDENTIALS_SECRET_NAME", "")
        env.set("OPENLAKEFORGE_CATALOG_FLOE_CLIENT_ID_KEY", "")
        env.set("OPENLAKEFORGE_CATALOG_FLOE_CLIENT_SECRET_KEY", "")

    catalog_om_service_name = "aws_glue" if is_glue else "polaris"
    catalog_name = env.get("OPENLAKEFORGE_CATALOG_NAME")
    database_fqn = f"{catalog_om_service_name}.{catalog_name}"
    default_silver_fqns = json.dumps(
        {product: f"{database_fqn}.{product}_silver" for product in _PRODUCTS},
        separators=(",", ":"),
    )
    default_gold_fqns = json.dumps(
        {product: f"{database_fqn}.{product}_gold" for product in _PRODUCTS},
        separators=(",", ":"),
    )
    if env.get("OPENLAKEFORGE_CATALOG_DATABASE_FQN") == "":
        env.set("OPENLAKEFORGE_CATALOG_DATABASE_FQN", database_fqn)
    if env.get("OPENLAKEFORGE_CATALOG_SILVER_SCHEMA_FQNS_JSON") == "":
        env.set("OPENLAKEFORGE_CATALOG_SILVER_SCHEMA_FQNS_JSON", default_silver_fqns)
    if env.get("OPENLAKEFORGE_CATALOG_GOLD_SCHEMA_FQNS_JSON") == "":
        env.set("OPENLAKEFORGE_CATALOG_GOLD_SCHEMA_FQNS_JSON", default_gold_fqns)
    if not om_catalog_service_user_set:
        env.set("OPENMETADATA_CATALOG_SERVICE", catalog_om_service_name)
    if env.get("OPENLAKEFORGE_STORAGE_IMPLEMENTATION") == "storage.aws_s3":
        if not storage_om_service_user_set:
            env.set("OPENLAKEFORGE_STORAGE_OM_SERVICE", "aws_s3")
        if not storage_display_name_user_set:
            env.set("OPENLAKEFORGE_STORAGE_DISPLAY_NAME", "AWS S3")
    else:
        if not storage_om_service_user_set:
            env.set("OPENLAKEFORGE_STORAGE_OM_SERVICE", "seaweedfs")
        if not storage_display_name_user_set:
            env.set("OPENLAKEFORGE_STORAGE_DISPLAY_NAME", "SeaweedFS S3")

    # Re-derive AWS_REGION from the *resolved* storage region. The first default
    # pass runs before provider contracts apply (storage region still at the
    # us-east-1 default), and the resolver runs in a subprocess that cannot see
    # a shell-local AWS_REGION, so without this a cloud stack would keep the
    # stale default. A caller that exported AWS_REGION still wins.
    if not aws_region_user_set:
        env.set("AWS_REGION", env.get("OPENLAKEFORGE_STORAGE_REGION"))
    if not aws_default_region_user_set:
        env.set("AWS_DEFAULT_REGION", env.get("AWS_REGION"))

    env.default("CODE_BUCKET_NAME", env.get("OPENLAKEFORGE_ARTIFACT_BUCKET_NAME"))
    env.default("OPENMETADATA_CATALOG_DATABASE", env.get("OPENLAKEFORGE_CATALOG_NAME"))
    if not query_uri_user_set:
        env.set(
            "OPENLAKEFORGE_QUERY_SQLALCHEMY_URI",
            "trino://superset@{host}:{port}/{catalog}".format(
                host=env.get("OPENLAKEFORGE_QUERY_TRINO_HOST"),
                port=env.get("OPENLAKEFORGE_QUERY_TRINO_PORT"),
                catalog=env.get("OPENLAKEFORGE_QUERY_TRINO_CATALOG"),
            ),
        )

    return env.exports, env.unsets


def render_shell_exports(exports: Mapping[str, str], unsets: list[str]) -> str:
    lines = [f"export {name}={shlex.quote(value)}" for name, value in exports.items()]
    lines.extend(f"unset {name}" for name in unsets)
    return "\n".join(lines)
