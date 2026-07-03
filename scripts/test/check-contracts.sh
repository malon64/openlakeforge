#!/usr/bin/env bash
set -euo pipefail

require_cmd() {
  local cmd="$1"
  if ! command -v "${cmd}" &>/dev/null; then
    printf "ERROR: '%s' not found on PATH\n" "${cmd}" >&2
    exit 1
  fi
}

require_cmd python3

echo "==> Contract source checks"
python3 <<'PY'
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

errors = []

contracts_tf = Path("infra/terraform/environments/local/contracts.tf")
text = contracts_tf.read_text()
azure_contracts_tf = Path("infra/terraform/environments/azure-poc/contracts.tf")
azure_text = azure_contracts_tf.read_text()
aws_contracts_tf = Path("infra/terraform/environments/aws-poc/contracts.tf")
aws_text = aws_contracts_tf.read_text()
local_main_tf = Path("infra/terraform/environments/local/main.tf")
local_main_text = local_main_tf.read_text()
azure_main_tf = Path("infra/terraform/environments/azure-poc/main.tf")
azure_main_text = azure_main_tf.read_text()
aws_main_tf = Path("infra/terraform/environments/aws-poc/main.tf")
aws_main_text = aws_main_tf.read_text()

required_contracts = [
    "foundation_contract",
    "kubernetes_platform_contract",
    "storage_contract",
    "metadata_database_contract",
    "catalog_contract",
    "artifact_registry_contract",
    "artifact_bucket_contract",
    "secrets_contract",
    "identity_contract",
    "access_contract",
    "observability_contract",
    "governance_contract",
    "reporting_contract",
    "query_contract",
    "orchestration_contract",
]
for name in required_contracts:
    if f"{name} =" not in text:
        errors.append(f"{contracts_tf}: missing {name}")
    if f"{name} =" not in azure_text:
        errors.append(f"{azure_contracts_tf}: missing {name}")
    if f"{name} =" not in aws_text:
        errors.append(f"{aws_contracts_tf}: missing {name}")

required_adapters = [
    "foundation.kind",
    "storage.s3_compatible.seaweedfs",
    "catalog.iceberg_rest.polaris",
    "metadata_database.postgresql.in_cluster",
    "secrets.kubernetes_secret",
    "artifacts.local_kind_and_s3",
    "observability.object_log_archive",
    "storage.aws_s3",
    "catalog.aws_glue",
    "metadata_database.aws_rds_postgresql",
    "secrets.aws_secrets_manager_or_external_secrets",
    "artifacts.ecr",
]
for adapter in required_adapters:
    if adapter not in text:
        errors.append(f"{contracts_tf}: missing adapter shape {adapter}")

required_azure_adapters = [
    "foundation.aks",
    "platform.kubernetes.aks",
    "storage.s3_compatible.seaweedfs_on_aks",
    "catalog.iceberg_rest.polaris_on_aks",
    "metadata_database.postgresql.in_cluster_on_aks",
    "secrets.kubernetes_secret_on_aks",
    "identity.azure_workload_identity_ready",
    "artifacts.azure_acr",
    "artifacts.azure_acr_and_s3_compatible_bucket",
    "observability.object_log_archive_on_aks",
    "access.kubectl_port_forward",
    "storage.azure_blob_or_adls_gen2",
    "catalog.polaris_with_azure_storage",
    "metadata_database.azure_postgresql_flexible_server",
    "secrets.azure_key_vault_external_secrets",
]
for adapter in required_azure_adapters:
    if adapter not in azure_text:
        errors.append(f"{azure_contracts_tf}: missing Azure adapter shape {adapter}")

required_aws_adapters = [
    "foundation.eks",
    "platform.kubernetes.eks",
    "storage.aws_s3",
    "catalog.aws_glue",
    "metadata_database.aws_rds_postgresql",
    "query.trino_on_eks",
    "orchestration.dagster_on_eks",
    "governance.openmetadata_on_eks",
    "reporting.superset_on_eks",
    "artifacts.aws_ecr",
    "artifacts.aws_s3_bucket",
    "artifacts.aws_ecr_and_s3",
    "secrets.kubernetes_secret_on_eks",
    "identity.aws_pod_identity",
    "observability.object_log_archive_on_eks",
]
for adapter in required_aws_adapters:
    if adapter not in aws_text:
        errors.append(f"{aws_contracts_tf}: missing AWS adapter shape {adapter}")

if 'implementation        = "storage.s3_compatible.seaweedfs_on_aks"' not in azure_text:
    errors.append(f"{azure_contracts_tf}: Azure POC must actively use SeaweedFS S3-compatible storage")
if 'implementation            = "artifacts.azure_acr"' not in azure_text:
    errors.append(f"{azure_contracts_tf}: Azure POC must actively use ACR for runtime images")
if re.search(r'implementation\s*=\s*"storage\.azure_', azure_text):
    errors.append(f"{azure_contracts_tf}: Azure Blob/ADLS must remain a future adapter, not the active POC storage implementation")
if 'local_upload_access_mode = "kubectl-port-forward"' not in azure_text:
    errors.append(f"{azure_contracts_tf}: Azure POC artifact bucket must keep kubectl port-forward upload mode")
for required in [
    'implementation       = "storage.aws_s3"',
    'implementation       = "metadata_database.aws_rds_postgresql"',
    'implementation             = "catalog.aws_glue"',
    'catalog_type               = "glue"',
    'catalog_provider           = "aws-glue"',
    'auth_mode                  = "aws-sigv4-pod-identity"',
    'distribution_mode        = "aws-s3-upload"',
    'local_upload_access_mode = "aws-cli"',
]:
    if required not in aws_text:
        errors.append(f"{aws_contracts_tf}: AWS POC must actively declare {required}")
if "storage.s3_compatible.seaweedfs" in aws_text:
    errors.append(f"{aws_contracts_tf}: AWS POC must replace SeaweedFS with S3")
if "catalog.iceberg_rest.polaris" in aws_text:
    errors.append(f"{aws_contracts_tf}: AWS POC must replace Polaris with Glue")
for contracts_path, contracts_body in [(contracts_tf, text), (azure_contracts_tf, azure_text), (aws_contracts_tf, aws_text)]:
    if 'floe_manifest_access_mode = "remote"' not in contracts_body:
        errors.append(f"{contracts_path}: orchestration contract must set remote Floe manifest access")
    if 'access_mode              = "remote"' not in contracts_body:
        errors.append(f"{contracts_path}: artifact bucket contract must expose remote Floe manifest access")
    if "ops_bucket_name" not in contracts_body:
        errors.append(f"{contracts_path}: artifact bucket contract must use ops_bucket_name")
    for required in [
        "artifact_base_uri",
        "floe_manifest_base_uri",
        "floe_report_base_uri",
        "log_base_uri",
        "run_artifact_base_uri",
        "ops_artifacts",
    ]:
        if required not in contracts_body:
            errors.append(f"{contracts_path}: missing ops artifact/observability field {required}")
    expected_log_mode = "s3-object-archive" if contracts_path == aws_contracts_tf else "s3-compatible-object-archive"
    if expected_log_mode not in contracts_body:
        errors.append(f"{contracts_path}: missing ops artifact/observability field {expected_log_mode}")
    for required_location in [
        'name               = "sales-dagster"',
        'definitions_module = "domains.sales.definitions"',
        'name               = "supply-chain-dagster"',
        'definitions_module = "domains.supply_chain.definitions"',
    ]:
        if required_location not in contracts_body:
            errors.append(f"{contracts_path}: missing domain Dagster code location {required_location}")
    for required in [
        "catalog_namespace_model",
        "catalog_namespaces",
        "catalog_schema_names",
        "silver_namespaces",
        "gold_namespaces",
        "silver_schema_fqns",
        "gold_schema_fqns",
    ]:
        if required not in contracts_body:
            errors.append(f"{contracts_path}: catalog contract must expose {required}")
    if re.search(r'\b(silver_namespace|gold_namespace)\s*=\s*"(silver|gold)"', contracts_body):
        errors.append(f"{contracts_path}: catalog contract must not expose shared silver/gold namespace fields")

expected_product_namespaces = {
    "sales_order_revenue": {
        "silver": "sales_order_revenue_silver",
        "gold": "sales_order_revenue_gold",
    },
    "sales_customer_health": {
        "silver": "sales_customer_health_silver",
        "gold": "sales_customer_health_gold",
    },
    "supply_chain_inventory_reliability": {
        "silver": "supply_chain_inventory_reliability_silver",
        "gold": "supply_chain_inventory_reliability_gold",
    },
}
for main_path, main_body in [(local_main_tf, local_main_text), (azure_main_tf, azure_main_text), (aws_main_tf, aws_main_text)]:
    if 'catalog_namespace_model = "product-layer"' not in main_body:
        errors.append(f"{main_path}: catalog namespace model must be product-layer")
    if main_path == aws_main_tf:
        if not re.search(r'\bcatalog_namespaces\s*=\s*local\.catalog_namespaces\b', main_body):
            errors.append(f"{main_path}: Glue module must receive product catalog namespaces")
    elif not re.search(r'\bcatalog_namespaces\s*=\s*local\.catalog_namespaces\b', main_body):
        errors.append(f"{main_path}: Polaris module must receive product catalog namespaces")
    if "catalog_schema_names" not in main_body or "[for namespace in local.catalog_namespaces : namespace.name]" not in main_body:
        errors.append(f"{main_path}: OpenMetadata module must seed all product catalog namespaces")
    for namespace_pair in expected_product_namespaces.values():
        for namespace in namespace_pair.values():
            if namespace not in main_body:
                errors.append(f"{main_path}: missing product catalog namespace {namespace}")
    if "ops_bucket_name" not in main_body or "floe/manifests" not in main_body:
        errors.append(f"{main_path}: must use the ops artifact bucket and floe/manifests prefix")
    if "var.code_bucket_name" in main_body:
        errors.append(f"{main_path}: must not use legacy code_bucket_name")

aws_glue_main_tf = Path("infra/terraform/modules/catalog/aws-glue/main.tf")
aws_glue_outputs_tf = Path("infra/terraform/modules/catalog/aws-glue/outputs.tf")
aws_glue_main_text = aws_glue_main_tf.read_text()
aws_glue_outputs_text = aws_glue_outputs_tf.read_text()
if 'resource "aws_glue_catalog_database" "namespace"' not in aws_glue_main_text:
    errors.append(f"{aws_glue_main_tf}: must create product-layer Glue databases")
if 'resource "aws_glue_catalog_database" "catalog"' in aws_glue_main_text:
    errors.append(f"{aws_glue_main_tf}: must not create the logical catalog alias as a Glue database")
if not re.search(r'resource\s+"aws_glue_catalog_database"\s+"namespace"[^{]*\{[^}]*for_each\s*=', aws_glue_main_text, re.DOTALL):
    errors.append(f"{aws_glue_main_tf}: product-layer Glue databases must use for_each over catalog namespaces")
for required in [
    "name         = each.value.name",
    "location_uri = each.value.location",
    "catalog_namespace_map = { for namespace in var.catalog_namespaces : namespace.name => namespace }",
    "catalog_schema_names  = keys(local.catalog_namespace_map)",
]:
    if required not in aws_glue_main_text:
        errors.append(f"{aws_glue_main_tf}: missing shared catalog/schema field {required}")
for required in [
    "glue_database                = null",
    "glue_database_location       = null",
    "glue_database_names          = local.catalog_schema_names",
    "glue_schema_names            = local.catalog_schema_names",
    "catalog_schema_names         = local.catalog_schema_names",
    "catalog_namespaces           = var.catalog_namespaces",
]:
    if required not in aws_glue_outputs_text:
        errors.append(f"{aws_glue_outputs_tf}: missing shared catalog/schema output {required}")

local_variables_tf = Path("infra/terraform/environments/local/variables.tf").read_text()
local_outputs_tf = Path("infra/terraform/environments/local/outputs.tf").read_text()
makefile_body = Path("Makefile").read_text()
local_infra_script = Path("scripts/local/stack/infra-up.sh").read_text()
local_setup_script = Path("scripts/local/stack/setup.sh").read_text()
seaweedfs_outputs = Path("infra/terraform/modules/storage/seaweedfs/outputs.tf").read_text()
for required in [
    "filer_service_name",
    "filer_http_port",
    "filer_endpoint",
    "master_service_name",
    "master_http_port",
    "master_endpoint",
]:
    if required not in seaweedfs_outputs:
        errors.append(f"infra/terraform/modules/storage/seaweedfs/outputs.tf: missing SeaweedFS UI contract field {required}")
for required in [
    "local-seaweed-ui-forward",
    "svc/seaweedfs-filer-client 8888:8888",
    "svc/seaweedfs-master 9333:9333",
]:
    if required not in makefile_body:
        errors.append(f"Makefile: missing SeaweedFS UI wrapper support {required}")
for path, body in [
    (Path("infra/terraform/environments/local/variables.tf"), local_variables_tf),
    (local_main_tf, local_main_text),
    (contracts_tf, text),
    (Path("infra/terraform/environments/local/outputs.tf"), local_outputs_tf),
    (Path("scripts/local/stack/infra-up.sh"), local_infra_script),
    (Path("scripts/local/stack/setup.sh"), local_setup_script),
]:
    legacy_dev_ui = "file" + "stash"
    legacy_dev_ui_env = "ENABLE_" + "FILESTASH"
    if legacy_dev_ui in body.lower() or legacy_dev_ui_env in body:
        errors.append(f"{path}: must not contain legacy S3 browser wiring")

required_checks = [
    'check "foundation_contract_matches_platform_context"',
    'check "local_contract_adapters_are_explicit"',
    'check "catalog_contract_consumer_support"',
    'check "openmetadata_catalog_fqn_uses_lakehouse_database"',
]
for check in required_checks:
    if check not in text:
        errors.append(f"{contracts_tf}: missing Terraform validation {check}")

required_azure_checks = [
    'check "foundation_contract_matches_platform_context"',
    'check "azure_contract_adapters_are_explicit"',
    'check "azure_poc_keeps_s3_compatible_storage"',
    'check "azure_poc_uses_acr_artifacts"',
    'check "catalog_contract_consumer_support"',
    'check "openmetadata_catalog_fqn_uses_lakehouse_database"',
]
for check in required_azure_checks:
    if check not in azure_text:
        errors.append(f"{azure_contracts_tf}: missing Terraform validation {check}")

required_aws_checks = [
    'check "foundation_contract_matches_platform_context"',
    'check "aws_contract_adapters_are_explicit"',
    'check "aws_poc_uses_managed_services"',
    'check "catalog_contract_consumer_support"',
]
for check in required_aws_checks:
    if check not in aws_text:
        errors.append(f"{aws_contracts_tf}: missing Terraform validation {check}")

for path in sorted(Path("domains").glob("*/contracts/floe/*.yml")):
    body = path.read_text()
    domain = path.parts[1]
    product = path.stem
    product_key = f"{domain}_{product}"
    expected_namespace = f'{product_key}_silver'
    if 'catalog: "polaris"' in body:
        errors.append(f"{path}: Floe contracts must use logical catalog iceberg_catalog, not physical polaris")
    if 'catalog: "iceberg_catalog"' not in body:
        errors.append(f"{path}: no logical iceberg_catalog sink reference found")
    if f'namespace: "{expected_namespace}"' not in body:
        errors.append(f"{path}: no product Silver namespace {expected_namespace} sink reference found")
    if 'namespace: "silver"' in body:
        errors.append(f"{path}: Floe contracts must use product Silver namespaces, not shared silver")
    if re.search(r'table: "(order_revenue|customer_health|inventory_reliability)_', body):
        errors.append(f"{path}: Silver table names must be product-local because the namespace carries the product")
    if 'storage: "lakehouse_s3"' in body:
        errors.append(f"{path}: Floe contracts must use medallion storage aliases, not legacy lakehouse_s3")
    if 'storage: "lakehouse_storage"' in body:
        errors.append(f"{path}: Floe contracts must use medallion storage aliases, not aggregate lakehouse_storage")
    if 'storage: "lakehouse_bronze"' not in body:
        errors.append(f"{path}: no logical lakehouse_bronze source reference found")
    if 'storage: "lakehouse_silver"' not in body:
        errors.append(f"{path}: no logical lakehouse_silver sink reference found")
    for required in [
        'storages:',
        'default: "lakehouse_bronze"',
        'name: "lakehouse_bronze"',
        'name: "lakehouse_silver"',
        'name: "openlakeforge_ops"',
        'bucket: "{{OPENLAKEFORGE_STORAGE_BRONZE_BUCKET}}"',
        'bucket: "{{OPENLAKEFORGE_STORAGE_SILVER_BUCKET}}"',
        'bucket: "{{OPENLAKEFORGE_OPS_BUCKET_NAME}}"',
        'region: "{{OPENLAKEFORGE_STORAGE_REGION}}"',
    ]:
        if required not in body:
            errors.append(f"{path}: Floe config must define schema-level medallion storage setting {required}")
    report_block = body.split("entities:", 1)[0]
    if 'storage: "openlakeforge_ops"' not in report_block:
        errors.append(f"{path}: Floe report storage must use openlakeforge_ops")
    if 'storage: "lakehouse_silver"' in report_block:
        errors.append(f"{path}: Floe report storage must not use lakehouse_silver")

for path in sorted(Path("domains").glob("*/transformations/dbt/*/profiles.yml")):
    body = path.read_text()
    parts = path.parts
    domain = parts[1]
    product = parts[4]
    expected_schema = f"{domain}_{product}_gold"
    if "{{PROFILE_NAME}}" in body or "{{GOLD_SCHEMA}}" in body:
        errors.append(f"{path}: product dbt profile must be rendered from libs/dbt/profiles, not left as a template")
    if "POLARIS_" in body:
        errors.append(f"{path}: product dbt profiles must use OPENLAKEFORGE_CATALOG_* env vars, not POLARIS_*")
    if "seaweedfs-s3" in body:
        errors.append(f"{path}: product dbt profiles must not hardcode local storage endpoints")
    if expected_schema not in body:
        errors.append(f"{path}: dbt profile must default to product Gold namespace {expected_schema}")
    if "OPENLAKEFORGE_DBT_SCHEMA" in body:
        errors.append(f"{path}: dbt profile must not use a global schema override")
    if "aws_runtime:" in body:
        errors.append(f"{path}: product dbt profiles must not inline AWS runtime settings; render them from libs/dbt/profiles/aws.yml")
    if "local_runtime:" not in body:
        errors.append(f"{path}: checked-in product dbt profiles must keep the local runtime target")
    for required in [
        "OPENLAKEFORGE_CATALOG_DBT_CLIENT_ID",
        "OPENLAKEFORGE_CATALOG_DBT_CLIENT_SECRET",
        "OPENLAKEFORGE_CATALOG_REST_URI",
        "OPENLAKEFORGE_CATALOG_WAREHOUSE",
        "OPENLAKEFORGE_DUCKDB_S3_ENDPOINT",
    ]:
        if required not in body:
            errors.append(f"{path}: missing runtime contract env var {required}")

dbt_profile_templates = {
    "local": Path("libs/dbt/profiles/local.yml"),
    "azure": Path("libs/dbt/profiles/azure.yml"),
    "aws": Path("libs/dbt/profiles/aws.yml"),
}
for name, path in dbt_profile_templates.items():
    body = path.read_text()
    for required in [
        "{{PROFILE_NAME}}",
        "{{DEFAULT_DUCKDB_PATH}}",
        "{{GOLD_SCHEMA}}",
        "OPENLAKEFORGE_CATALOG_WAREHOUSE",
    ]:
        if required not in body:
            errors.append(f"{path}: missing shared dbt profile template field {required}")
    if name == "aws":
        for required in [
            "aws_runtime:",
            "provider: credential_chain",
            "OPENLAKEFORGE_CATALOG_GLUE_CATALOG_ID",
            "OPENLAKEFORGE_CATALOG_GLUE_REST_URI",
            "endpoint_type: glue",
            "alias: \"{{ env_var('OPENLAKEFORGE_CATALOG_WAREHOUSE', env_var('OPENLAKEFORGE_CATALOG_GLUE_DATABASE', 'lakehouse_dev')) }}\"",
        ]:
            if required not in body:
                errors.append(f"{path}: missing AWS Glue/SigV4 dbt runtime setting {required}")
        for forbidden in ["authorization_type", "signing_region", "signing_name"]:
            if forbidden in body:
                errors.append(f"{path}: endpoint_type glue must not be combined with {forbidden}")
    elif "aws_runtime:" in body:
        errors.append(f"{path}: non-AWS dbt profile templates must not define aws_runtime")

dbt_profile_renderer = Path("libs/dbt/render_profiles.py").read_text()
for required in [
    "infer_environment",
    "ensure_runtime_profile_dir",
    "OPENLAKEFORGE_DBT_PROFILE_ENV",
    "OPENLAKEFORGE_CATALOG_TYPE",
    "OPENLAKEFORGE_STORAGE_PROVIDER",
]:
    if required not in dbt_profile_renderer:
        errors.append(f"libs/dbt/render_profiles.py: missing renderer feature {required}")

for path in sorted(Path("domains").glob("*/transformations/dbt/*/dbt_project.yml")):
    body = path.read_text()
    if re.search(r"^\s*\+database:\s*lakehouse_dev\s*$", body, re.MULTILINE):
        errors.append(f"{path}: +database must use OPENLAKEFORGE_CATALOG_WAREHOUSE")

for path in sorted(Path("domains").glob("*/transformations/dbt/*/models/sources.yml")):
    body = path.read_text()
    parts = path.parts
    domain = parts[1]
    product = parts[4]
    expected_schema = f"{domain}_{product}_silver"
    if re.search(r"^\s*database:\s*lakehouse_dev\s*$", body, re.MULTILINE):
        errors.append(f"{path}: source database must use OPENLAKEFORGE_CATALOG_WAREHOUSE")
    if f"schema: {expected_schema}" not in body:
        errors.append(f"{path}: source schema must be product Silver namespace {expected_schema}")
    if "OPENLAKEFORGE_CATALOG_SILVER_NAMESPACE" in body:
        errors.append(f"{path}: source schema must not use the legacy global Silver namespace env var")
    if re.search(r"identifier:\s+(order_revenue|customer_health|inventory_reliability)_", body):
        errors.append(f"{path}: source identifiers must be product-local because the schema carries the product")

for path in [
    Path("scripts/local/artifacts/floe-manifest.sh"),
    Path("scripts/local/artifacts/upload-floe-manifest.sh"),
    Path("scripts/local/artifacts/dbt-parse.sh"),
    Path("scripts/local/artifacts/superset-reports-deploy.sh"),
    Path("scripts/local/artifacts/openmetadata-metadata-deploy.sh"),
    Path("scripts/test/check-dbt.sh"),
]:
    body = path.read_text()
    if "scripts/local/contracts/load-runtime-env.sh" not in body:
        errors.append(f"{path}: must source the runtime contract environment")
for path in [Path("scripts/local/artifacts/dbt-parse.sh"), Path("scripts/test/check-dbt.sh")]:
    body = path.read_text()
    if "libs.dbt.render_profiles" not in body:
        errors.append(f"{path}: must render dbt profiles from libs/dbt/profiles before invoking dbt")

floe_manifest_script = Path("scripts/local/artifacts/floe-manifest.sh")
floe_manifest_body = floe_manifest_script.read_text()
for required in [
    "FLOE_RUNTIME_PROFILE_URI",
    "libs/floe/profiles/aws-eks.yml",
    "OPENLAKEFORGE_STORAGE_IMPLEMENTATION",
    "OPENLAKEFORGE_CATALOG_TYPE",
    "OPENLAKEFORGE_CATALOG_PROVIDER",
    "FLOE_PERSIST_RUNTIME_ARTIFACTS",
    "floe_path",
    "silver_namespace_for_product",
    "OPENLAKEFORGE_CATALOG_SILVER_NAMESPACES_JSON",
    "OPENLAKEFORGE_CATALOG_GLUE_DATABASE",
    'profiles/${domain}/${product}',
    '-v "${REPO_ROOT}:/work"',
    '"${FLOE_RUNTIME_ARTIFACT_DIR}/manifests"',
    "--manifest-path-mode resolved-uri",
]:
    if required not in floe_manifest_body:
        errors.append(f"{floe_manifest_script}: missing provider-aware Floe runtime profile handling {required}")
for forbidden in [
    "adapt_manifest_runtime_args",
    "render-floe-config.py",
    "FLOE_REMOTE_CONFIG_BASE_URI",
    "FLOE_REMOTE_PROFILE_URI",
    "payload[\"execution\"]",
    "payload[\"config_uri\"]",
    "payload[\"profile_uri\"]",
    "https://github.com/malon64/floe/issues/424",
    "https://github.com/malon64/floe/issues/425",
]:
    if forbidden in floe_manifest_body:
        errors.append(f"{floe_manifest_script}: must not patch Floe-generated manifests with {forbidden}")

dagster_values = Path("infra/helm/values/local/dagster.yaml").read_text()
for required in [
    "S3ComputeLogManager",
    "openlakeforge-ops",
    "logs/dagster/compute",
]:
    if required not in dagster_values:
        errors.append(f"infra/helm/values/local/dagster.yaml: missing S3 compute log setting {required}")

dagster_module = Path("infra/terraform/modules/orchestration/dagster/main.tf").read_text()
for required in [
    "S3ComputeLogManager",
    "kubernetes_cron_job_v1",
    "openlakeforge-k8s-log-archive",
    "local.code_location_deployments",
    "OPENLAKEFORGE_FLOE_REPORT_BASE_URI",
    "OPENLAKEFORGE_RUN_ARTIFACT_BASE_URI",
    "OPENLAKEFORGE_STORAGE_BRONZE_BUCKET",
    "OPENLAKEFORGE_STORAGE_SILVER_BUCKET",
    "OPENLAKEFORGE_STORAGE_GOLD_BUCKET",
    "OPENLAKEFORGE_CATALOG_GLUE_CATALOG_ID",
    "OPENLAKEFORGE_CATALOG_GLUE_REST_WAREHOUSE",
    "OPENLAKEFORGE_CATALOG_GLUE_DATABASE",
    "OPENLAKEFORGE_CATALOG_GLUE_WAREHOUSE_PREFIX",
    "OPENLAKEFORGE_DBT_PROFILE_ENV",
    "local.dbt_profile_env",
    "/tmp/openlakeforge-dbt-profiles",
]:
    if required not in dagster_module:
        errors.append(f"infra/terraform/modules/orchestration/dagster/main.tf: missing {required}")

product_dagster_body = Path("libs/product_dagster.py").read_text()
for required in [
    "ensure_runtime_profile_dir",
    "_ensure_dbt_profiles_dir",
    "profiles_dir=str(dbt_profiles_dir)",
]:
    if required not in product_dagster_body:
        errors.append(f"libs/product_dagster.py: must render environment-specific dbt profiles at runtime using {required}")

project_code_dockerfile = Path("images/project-code/Dockerfile").read_text()
for required in [
    "ARG DBT_PROFILE_ENV=local",
    "python -m libs.dbt.render_profiles --environment",
    "--profiles-dir \"${project_dir}\" --target local",
]:
    if required not in project_code_dockerfile:
        errors.append(f"images/project-code/Dockerfile: must render selected dbt profile before dbt manifest parse using {required}")

aws_floe_profile = Path("libs/floe/profiles/aws-eks.yml")
aws_floe_profile_body = aws_floe_profile.read_text()
for required in [
    'name: "aws-eks"',
    'variables:',
    'OPENLAKEFORGE_STORAGE_BRONZE_BUCKET: "openlakeforge-poc-bronze"',
    'OPENLAKEFORGE_STORAGE_SILVER_BUCKET: "openlakeforge-poc-silver"',
    'OPENLAKEFORGE_OPS_BUCKET_NAME: "openlakeforge-poc-ops"',
    'OPENLAKEFORGE_STORAGE_REGION: "eu-west-1"',
    'type: "glue"',
    'region: "eu-west-1"',
    'database: "sales_customer_health_silver"',
    'warehouse_storage: "lakehouse_silver"',
    'warehouse_prefix: "warehouse/iceberg"',
    'create_database_if_missing: false',
    'AWS_S3_FORCE_PATH_STYLE: "false"',
    'secrets: []',
]:
    if required not in aws_floe_profile_body:
        errors.append(f"{aws_floe_profile}: missing native AWS Glue/S3 profile setting {required}")
for forbidden in [
    "polaris",
    "seaweedfs",
    "AWS_ENDPOINT_URL",
    "OPENLAKEFORGE_CATALOG_FLOE_CLIENT_ID",
    'type: "rest"',
    "OPENLAKEFORGE_CATALOG_GLUE_REST_URI",
    "authorization_type",
    "signing_name",
    "signing_region",
    "\nstorages:",
]:
    if forbidden in aws_floe_profile_body:
        errors.append(f"{aws_floe_profile}: must not contain local Polaris/SeaweedFS setting {forbidden}")

for floe_profile in [Path("libs/floe/profiles/local-k8s.yml"), aws_floe_profile]:
    body = floe_profile.read_text()
    for forbidden in ["\nstorages:", "\nlineage:"]:
        if forbidden in body:
            errors.append(f"{floe_profile}: profile must follow Floe profile.schema.yaml and not contain {forbidden.strip()}")
    for required in ["apiVersion: floe/v1", "kind: EnvironmentProfile", "metadata:", "variables:", "catalogs:", "execution:", "validation:"]:
        if required not in body:
            errors.append(f"{floe_profile}: missing Floe profile.schema.yaml section {required}")

openmetadata_metadata_script = Path("scripts/local/artifacts/openmetadata-metadata-deploy.sh")
openmetadata_metadata_body = openmetadata_metadata_script.read_text()
for required in [
    "OPENLAKEFORGE_STORAGE_BRONZE_BUCKET",
    "OPENLAKEFORGE_STORAGE_SILVER_BUCKET",
    "OPENLAKEFORGE_STORAGE_GOLD_BUCKET",
    "OPENLAKEFORGE_CATALOG_DATABASE_FQN",
    "OPENLAKEFORGE_CATALOG_SILVER_SCHEMA_FQNS_JSON",
    "OPENLAKEFORGE_CATALOG_GOLD_SCHEMA_FQNS_JSON",
    "CATALOG_SILVER_SCHEMA_FQNS",
    "CATALOG_GOLD_SCHEMA_FQNS",
    "provider_asset_fqn",
    "storage_bucket_specs",
]:
    if required not in openmetadata_metadata_body:
        errors.append(f"{openmetadata_metadata_script}: must seed OpenMetadata medallion bucket containers using {required}")
if "product_bronze_containers" in openmetadata_metadata_body:
    errors.append(f"{openmetadata_metadata_script}: must not seed path-level product Bronze containers")
if "f\"{STORAGE_SERVICE}.{STORAGE_BUCKET}\"" in openmetadata_metadata_body:
    errors.append(f"{openmetadata_metadata_script}: must not parent product containers under a single storage bucket")

load_runtime_env_script = Path("scripts/local/contracts/load-runtime-env.sh")
load_runtime_env_body = load_runtime_env_script.read_text()
for required in [
    "OPENLAKEFORGE_CATALOG_DATABASE_FQN",
    "OPENLAKEFORGE_CATALOG_SILVER_SCHEMA_FQNS_JSON",
    "OPENLAKEFORGE_CATALOG_GOLD_SCHEMA_FQNS_JSON",
    "OPENMETADATA_CATALOG_SERVICE",
    "aws_glue",
    "OPENLAKEFORGE_CATALOG_GLUE_REST_WAREHOUSE",
    "OPENLAKEFORGE_STORAGE_OM_SERVICE",
    "OPENLAKEFORGE_STORAGE_DISPLAY_NAME",
]:
    if required not in load_runtime_env_body:
        errors.append(f"{load_runtime_env_script}: must emit OpenMetadata provider runtime setting {required}")

emit_contract_env_script = Path("scripts/local/contracts/emit-contract-env.py")
emit_contract_env_body = emit_contract_env_script.read_text()
for required in [
    "OPENLAKEFORGE_CATALOG_DATABASE_FQN",
    "OPENLAKEFORGE_CATALOG_SILVER_SCHEMA_FQNS_JSON",
    "OPENLAKEFORGE_CATALOG_GOLD_SCHEMA_FQNS_JSON",
    "OPENLAKEFORGE_CATALOG_GLUE_REST_WAREHOUSE",
    "is_glue_catalog",
    "catalog_warehouse",
    "catalog_database_fqn",
    "silver_schema_fqns",
    "gold_schema_fqns",
]:
    if required not in emit_contract_env_body:
        errors.append(f"{emit_contract_env_script}: must export OpenMetadata catalog FQN contract field {required}")

azure_artifact_script = Path("scripts/azure/stack/deploy-artifacts.sh")
azure_artifact_body = azure_artifact_script.read_text()
aws_artifact_script = Path("scripts/aws/stack/deploy-artifacts.sh")
aws_artifact_body = aws_artifact_script.read_text()
local_artifact_script = Path("scripts/local/stack/deploy-artifacts.sh")
local_artifact_body = local_artifact_script.read_text()
local_image_call = re.search(r"^prepare_local_project_code_image$", local_artifact_body, re.MULTILINE)
if local_image_call is None:
    errors.append(f"{local_artifact_script}: must call prepare_local_project_code_image")
elif local_artifact_body.find("floe-manifest.sh") > local_image_call.start():
    errors.append(f"{local_artifact_script}: must generate Floe manifests before building/loading the project-code image")
if "infra/terraform/environments/azure-poc" not in azure_artifact_body:
    errors.append(f"{azure_artifact_script}: must default runtime contracts to the Azure POC Terraform root")
if "OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR" not in azure_artifact_body:
    errors.append(f"{azure_artifact_script}: must pass OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR to reused artifact scripts")
if azure_artifact_body.find("floe-manifest.sh") > azure_artifact_body.find("build-push-project-code.sh"):
    errors.append(f"{azure_artifact_script}: must generate Floe manifests before building the project-code image")
if "update_dagster_project_code_image" not in azure_artifact_body:
    errors.append(f"{azure_artifact_script}: must update Dagster deployments to the project-code image pushed by azure-artifacts-deploy")
if "PROJECT_CODE_IMAGE=\"${PROJECT_CODE_IMAGE_REPOSITORY}:${PROJECT_CODE_IMAGE_TAG}\"" not in azure_artifact_body:
    errors.append(f"{azure_artifact_script}: must derive the exact pushed project-code image")
for path, expected_env in [
    (Path("scripts/local/images/build-project-code.sh"), "local"),
    (Path("scripts/azure/images/build-push-project-code.sh"), "azure"),
    (Path("scripts/aws/images/build-push-project-code.sh"), "aws"),
]:
    body = path.read_text()
    if f'PROJECT_CODE_DBT_PROFILE_ENV="${{PROJECT_CODE_DBT_PROFILE_ENV:-{expected_env}}}"' not in body:
        errors.append(f"{path}: must default project-code dbt profile rendering to {expected_env}")
    if '--build-arg "DBT_PROFILE_ENV=${PROJECT_CODE_DBT_PROFILE_ENV}"' not in body:
        errors.append(f"{path}: must pass DBT_PROFILE_ENV into the project-code Docker build")
if "regular containers to patch" not in azure_artifact_body or '"containers": [' not in azure_artifact_body:
    errors.append(f"{azure_artifact_script}: must patch only regular container images before rollout restart")
if "dagster-instance" not in azure_artifact_body or "job_image:" not in azure_artifact_body:
    errors.append(f"{azure_artifact_script}: must patch Dagster run launcher job_image before rollout restart")
if "patch_cronjob_image_if_exists" not in azure_artifact_body or "openlakeforge-k8s-log-archive" not in azure_artifact_body:
    errors.append(f"{azure_artifact_script}: must patch the Kubernetes log archive CronJob image after pushing project-code")
if "infra/terraform/environments/aws-poc" not in aws_artifact_body:
    errors.append(f"{aws_artifact_script}: must default runtime contracts to the AWS POC Terraform root")
if "aws s3api put-object" not in aws_artifact_body or "floe/manifests/${domain}/${product}/${product}.manifest.json" not in aws_artifact_body:
    errors.append(f"{aws_artifact_script}: must upload product Floe manifests directly to the AWS S3 ops bucket")
for required in [
    "manifest_root",
    "FLOE_PERSIST_RUNTIME_ARTIFACTS",
]:
    if required not in aws_artifact_body:
        errors.append(f"{aws_artifact_script}: must generate and upload Floe-generated AWS manifests using {required}")
for forbidden in [
    "upload_floe_runtime_artifacts_to_s3",
    "floe/configs",
    "floe/profiles/aws-eks.yml",
    "FLOE_REMOTE_CONFIG_BASE_URI",
    "FLOE_REMOTE_PROFILE_URI",
]:
    if forbidden in aws_artifact_body:
        errors.append(f"{aws_artifact_script}: must not upload obsolete AWS Floe runtime config/profile artifacts using {forbidden}")
if 'find domains -path "*/contracts/floe/manifests/*.manifest.json"' in aws_artifact_body:
    errors.append(f"{aws_artifact_script}: must upload rendered AWS manifests from the runtime artifact directory, not tracked local manifests")
if "build-push-project-code.sh" not in aws_artifact_body:
    errors.append(f"{aws_artifact_script}: must build and push the AWS project-code image")
if "PROJECT_CODE_IMAGE=\"${PROJECT_CODE_IMAGE_REPOSITORY}:${PROJECT_CODE_IMAGE_TAG}\"" not in aws_artifact_body:
    errors.append(f"{aws_artifact_script}: must derive the exact pushed project-code image")
if "regular containers to patch" not in aws_artifact_body or '"containers": [' not in aws_artifact_body:
    errors.append(f"{aws_artifact_script}: must patch only regular container images before rollout restart")
if "dagster-instance" not in aws_artifact_body or "job_image:" not in aws_artifact_body:
    errors.append(f"{aws_artifact_script}: must patch Dagster run launcher job_image before rollout restart")
if "patch_cronjob_image_if_exists" not in aws_artifact_body or "openlakeforge-k8s-log-archive" not in aws_artifact_body:
    errors.append(f"{aws_artifact_script}: must patch the Kubernetes log archive CronJob image after pushing project-code")
for path, body in [(azure_artifact_script, azure_artifact_body), (aws_artifact_script, aws_artifact_body)]:
    if "kubectl set image" in body or "*=${PROJECT_CODE_IMAGE}" in body:
        errors.append(f"{path}: must not use wildcard image patching because it rewrites chart-managed init containers")
for path, body in [(local_artifact_script, local_artifact_body), (azure_artifact_script, azure_artifact_body), (aws_artifact_script, aws_artifact_body)]:
    if "dagster-user-deployments-.+-dagster" not in body:
        errors.append(f"{path}: must discover domain Dagster user deployments instead of hardcoding names")
if "floe/manifests/%s/%s/%s.manifest.json" not in Path("scripts/local/artifacts/upload-floe-manifest.sh").read_text():
    errors.append("scripts/local/artifacts/upload-floe-manifest.sh: must upload manifests under floe/manifests")

with tempfile.NamedTemporaryFile("w+", encoding="utf-8") as handle:
    subprocess.run(
        ["python3", "scripts/local/contracts/render-floe-profile.py"],
        check=True,
        stdout=handle,
    )
    handle.seek(0)
    profile = handle.read()

if 'default: "iceberg_catalog"' not in profile:
    errors.append("rendered Floe profile must use logical catalog iceberg_catalog")
for required in [
    'variables:',
    'OPENLAKEFORGE_STORAGE_BRONZE_BUCKET: "lakehouse-bronze"',
    'OPENLAKEFORGE_STORAGE_SILVER_BUCKET: "lakehouse-silver"',
    'OPENLAKEFORGE_OPS_BUCKET_NAME: "openlakeforge-ops"',
    'OPENLAKEFORGE_STORAGE_REGION: "us-east-1"',
    'warehouse_storage: "lakehouse_silver"',
    'warehouse_prefix: "warehouse/iceberg"',
]:
    if required not in profile:
        errors.append(f"rendered Floe profile must include schema-valid setting {required}")
if "\nstorages:" in profile:
    errors.append("rendered Floe profile must not define profile-level storages; storages belong in the Floe config")
if "${OPENLAKEFORGE_CATALOG_FLOE_CLIENT_ID}" not in profile:
    errors.append("rendered Floe profile must use generic Floe catalog client env vars")
if "POLARIS_FLOE_CLIENT_ID" in profile.split("secrets:", 1)[0]:
    errors.append("rendered Floe profile catalog credential must not use POLARIS_* env vars")

aws_profile_env = os.environ.copy()
aws_profile_env.update(
    {
        "OPENLAKEFORGE_STORAGE_IMPLEMENTATION": "storage.aws_s3",
        "OPENLAKEFORGE_STORAGE_PROVIDER": "aws",
        "OPENLAKEFORGE_STORAGE_REGION": "eu-west-1",
        "OPENLAKEFORGE_STORAGE_ENDPOINT": "",
        "OPENLAKEFORGE_STORAGE_VIRTUAL_HOST_ENDPOINT": "",
        "OPENLAKEFORGE_STORAGE_PATH_STYLE_ACCESS": "false",
        "OPENLAKEFORGE_STORAGE_SSL_MODE": "required",
        "OPENLAKEFORGE_STORAGE_CREDENTIALS_SECRET_NAME": "",
        "OPENLAKEFORGE_STORAGE_BRONZE_BUCKET": "openlakeforge-poc-bronze",
        "OPENLAKEFORGE_STORAGE_SILVER_BUCKET": "openlakeforge-poc-silver",
        "OPENLAKEFORGE_OPS_BUCKET_NAME": "openlakeforge-poc-ops",
        "OPENLAKEFORGE_CATALOG_TYPE": "glue",
        "OPENLAKEFORGE_CATALOG_PROVIDER": "aws-glue",
        "OPENLAKEFORGE_CATALOG_NAME": "lakehouse",
        "OPENLAKEFORGE_CATALOG_WAREHOUSE": "lakehouse",
        "OPENLAKEFORGE_CATALOG_GLUE_CATALOG_ID": "123456789012",
        "OPENLAKEFORGE_CATALOG_GLUE_REST_WAREHOUSE": "123456789012",
        "OPENLAKEFORGE_CATALOG_GLUE_REGION": "eu-west-1",
        "OPENLAKEFORGE_CATALOG_GLUE_REST_URI": "https://glue.eu-west-1.amazonaws.com/iceberg",
        "OPENLAKEFORGE_CATALOG_GLUE_DATABASE": "sales_customer_health_silver",
        "OPENLAKEFORGE_CATALOG_GLUE_WAREHOUSE_PREFIX": "warehouse/iceberg",
    }
)
with tempfile.NamedTemporaryFile("w+", encoding="utf-8") as handle:
    subprocess.run(
        ["python3", "scripts/local/contracts/render-floe-profile.py"],
        check=True,
        stdout=handle,
        env=aws_profile_env,
    )
    handle.seek(0)
    aws_profile = handle.read()

for required in [
    'name: "aws-eks"',
    'type: "glue"',
    'region: "eu-west-1"',
    'database: "sales_customer_health_silver"',
    'warehouse_storage: "lakehouse_silver"',
    'warehouse_prefix: "s3://openlakeforge-poc-silver/warehouse/iceberg"',
    'create_database_if_missing: false',
    'OPENLAKEFORGE_STORAGE_BRONZE_BUCKET: "openlakeforge-poc-bronze"',
    'OPENLAKEFORGE_STORAGE_SILVER_BUCKET: "openlakeforge-poc-silver"',
    'OPENLAKEFORGE_OPS_BUCKET_NAME: "openlakeforge-poc-ops"',
    'OPENLAKEFORGE_STORAGE_REGION: "eu-west-1"',
    'AWS_S3_FORCE_PATH_STYLE: "false"',
    'AWS_EC2_METADATA_DISABLED: "false"',
    'secrets: []',
]:
    if required not in aws_profile:
        errors.append(f"rendered AWS Floe profile must include native Glue/S3 setting {required}")
if 'warehouse_prefix: "warehouse/iceberg"' in aws_profile:
    errors.append("rendered AWS Floe profile must use an absolute S3 warehouse_prefix for remote profile execution")
if "\nstorages:" in aws_profile:
    errors.append("rendered AWS Floe profile must not define profile-level storages; storages belong in the Floe config")
if "AWS_ENDPOINT_URL" in aws_profile:
    errors.append("rendered AWS Floe profile must not set SeaweedFS/S3-compatible endpoint env vars")
for forbidden in [
    'type: "rest"',
    "authorization_type",
    "signing_name",
    "signing_region",
    "OPENLAKEFORGE_CATALOG_GLUE_REST_URI",
    "https://glue.eu-west-1.amazonaws.com/iceberg",
]:
    if forbidden in aws_profile:
        errors.append(f"rendered AWS Floe profile must not include REST/SigV4 catalog setting {forbidden}")

tracked_files = subprocess.check_output(["git", "ls-files"], text=True).splitlines()
legacy_bucket = "openlakeforge-" + "code"
for tracked_file in tracked_files:
    path = Path(tracked_file)
    if not path.is_file():
        continue
    if legacy_bucket in path.read_text(errors="ignore"):
        errors.append(f"{path}: must not reference the legacy ops bucket name")

if errors:
    for error in errors:
        print(error, file=sys.stderr)
    sys.exit(1)
PY

echo "Contract compatibility checks passed."
