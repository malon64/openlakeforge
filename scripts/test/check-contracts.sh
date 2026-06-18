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
local_main_tf = Path("infra/terraform/environments/local/main.tf")
local_main_text = local_main_tf.read_text()
azure_main_tf = Path("infra/terraform/environments/azure-poc/main.tf")
azure_main_text = azure_main_tf.read_text()

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

required_adapters = [
    "foundation.kind",
    "storage.s3_compatible.seaweedfs",
    "catalog.iceberg_rest.polaris",
    "metadata_database.postgresql.in_cluster",
    "secrets.kubernetes_secret",
    "artifacts.local_kind_and_s3",
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
    "access.kubectl_port_forward",
    "storage.azure_blob_or_adls_gen2",
    "catalog.polaris_with_azure_storage",
    "metadata_database.azure_postgresql_flexible_server",
    "secrets.azure_key_vault_external_secrets",
]
for adapter in required_azure_adapters:
    if adapter not in azure_text:
        errors.append(f"{azure_contracts_tf}: missing Azure adapter shape {adapter}")

if 'implementation        = "storage.s3_compatible.seaweedfs_on_aks"' not in azure_text:
    errors.append(f"{azure_contracts_tf}: Azure POC must actively use SeaweedFS S3-compatible storage")
if 'implementation            = "artifacts.azure_acr"' not in azure_text:
    errors.append(f"{azure_contracts_tf}: Azure POC must actively use ACR for runtime images")
if re.search(r'implementation\s*=\s*"storage\.azure_', azure_text):
    errors.append(f"{azure_contracts_tf}: Azure Blob/ADLS must remain a future adapter, not the active POC storage implementation")
if 'local_upload_access_mode = "kubectl-port-forward"' not in azure_text:
    errors.append(f"{azure_contracts_tf}: Azure POC artifact bucket must keep kubectl port-forward upload mode")
for contracts_path, contracts_body in [(contracts_tf, text), (azure_contracts_tf, azure_text)]:
    if 'floe_manifest_access_mode = "remote"' not in contracts_body:
        errors.append(f"{contracts_path}: orchestration contract must set remote Floe manifest access")
    if 'access_mode              = "remote"' not in contracts_body:
        errors.append(f"{contracts_path}: artifact bucket contract must expose remote Floe manifest access")
    for required in [
        "catalog_namespace_model",
        "catalog_namespaces",
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
for main_path, main_body in [(local_main_tf, local_main_text), (azure_main_tf, azure_main_text)]:
    if 'catalog_namespace_model = "product-layer"' not in main_body:
        errors.append(f"{main_path}: catalog namespace model must be product-layer")
    if "catalog_namespaces   = local.catalog_namespaces" not in main_body:
        errors.append(f"{main_path}: Polaris module must receive product catalog namespaces")
    if "catalog_schema_names = [for namespace in local.catalog_namespaces : namespace.name]" not in main_body:
        errors.append(f"{main_path}: OpenMetadata module must seed all product catalog namespaces")
    for namespace_pair in expected_product_namespaces.values():
        for namespace in namespace_pair.values():
            if namespace not in main_body:
                errors.append(f"{main_path}: missing product catalog namespace {namespace}")

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
        errors.append(f"{path}: no logical lakehouse_silver sink/report reference found")

for path in sorted(Path("domains").glob("*/transformations/dbt/*/profiles.yml")):
    body = path.read_text()
    parts = path.parts
    domain = parts[1]
    product = parts[4]
    expected_schema = f"{domain}_{product}_gold"
    if "POLARIS_" in body:
        errors.append(f"{path}: product dbt profiles must use OPENLAKEFORGE_CATALOG_* env vars, not POLARIS_*")
    if "seaweedfs-s3" in body:
        errors.append(f"{path}: product dbt profiles must not hardcode local storage endpoints")
    if expected_schema not in body:
        errors.append(f"{path}: dbt profile must default to product Gold namespace {expected_schema}")
    if "OPENLAKEFORGE_DBT_SCHEMA" in body:
        errors.append(f"{path}: dbt profile must not use a global schema override")
    for required in [
        "OPENLAKEFORGE_CATALOG_DBT_CLIENT_ID",
        "OPENLAKEFORGE_CATALOG_DBT_CLIENT_SECRET",
        "OPENLAKEFORGE_CATALOG_REST_URI",
        "OPENLAKEFORGE_CATALOG_WAREHOUSE",
        "OPENLAKEFORGE_DUCKDB_S3_ENDPOINT",
    ]:
        if required not in body:
            errors.append(f"{path}: missing runtime contract env var {required}")

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

openmetadata_metadata_script = Path("scripts/local/artifacts/openmetadata-metadata-deploy.sh")
openmetadata_metadata_body = openmetadata_metadata_script.read_text()
for required in [
    "OPENLAKEFORGE_STORAGE_BRONZE_BUCKET",
    "OPENLAKEFORGE_STORAGE_SILVER_BUCKET",
    "OPENLAKEFORGE_STORAGE_GOLD_BUCKET",
    "storage_bucket_specs",
]:
    if required not in openmetadata_metadata_body:
        errors.append(f"{openmetadata_metadata_script}: must seed OpenMetadata medallion bucket containers using {required}")
if "product_bronze_containers" in openmetadata_metadata_body:
    errors.append(f"{openmetadata_metadata_script}: must not seed path-level product Bronze containers")
if "f\"{STORAGE_SERVICE}.{STORAGE_BUCKET}\"" in openmetadata_metadata_body:
    errors.append(f"{openmetadata_metadata_script}: must not parent product containers under a single storage bucket")

azure_artifact_script = Path("scripts/azure/stack/deploy-artifacts.sh")
azure_artifact_body = azure_artifact_script.read_text()
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
if "kubectl set image" not in azure_artifact_body or "*=${PROJECT_CODE_IMAGE}" not in azure_artifact_body:
    errors.append(f"{azure_artifact_script}: must patch Dagster deployment images before rollout restart")
if "dagster-instance" not in azure_artifact_body or "job_image:" not in azure_artifact_body:
    errors.append(f"{azure_artifact_script}: must patch Dagster run launcher job_image before rollout restart")

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
if 'default: "lakehouse_bronze"' not in profile:
    errors.append("rendered Floe profile must default to logical storage lakehouse_bronze")
for storage_alias in ['name: "lakehouse_bronze"', 'name: "lakehouse_silver"']:
    if storage_alias not in profile:
        errors.append(f"rendered Floe profile must define logical storage {storage_alias}")
if "${OPENLAKEFORGE_CATALOG_FLOE_CLIENT_ID}" not in profile:
    errors.append("rendered Floe profile must use generic Floe catalog client env vars")
if "POLARIS_FLOE_CLIENT_ID" in profile.split("secrets:", 1)[0]:
    errors.append("rendered Floe profile catalog credential must not use POLARIS_* env vars")

if errors:
    for error in errors:
        print(error, file=sys.stderr)
    sys.exit(1)
PY

echo "Contract compatibility checks passed."
