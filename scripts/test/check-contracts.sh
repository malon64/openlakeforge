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

required_checks = [
    'check "foundation_contract_matches_platform_context"',
    'check "local_contract_adapters_are_explicit"',
    'check "catalog_contract_consumer_support"',
    'check "openmetadata_catalog_fqn_uses_lakehouse_database"',
]
for check in required_checks:
    if check not in text:
        errors.append(f"{contracts_tf}: missing Terraform validation {check}")

for path in sorted(Path("domains").glob("*/contracts/floe/*.yml")):
    body = path.read_text()
    if 'catalog: "polaris"' in body:
        errors.append(f"{path}: Floe contracts must use logical catalog iceberg_catalog, not physical polaris")
    if 'catalog: "iceberg_catalog"' not in body:
        errors.append(f"{path}: no logical iceberg_catalog sink reference found")
    if 'storage: "lakehouse_s3"' in body:
        errors.append(f"{path}: Floe contracts must use logical storage lakehouse_storage, not legacy lakehouse_s3")
    if 'storage: "lakehouse_storage"' not in body:
        errors.append(f"{path}: no logical lakehouse_storage reference found")

for path in sorted(Path("domains").glob("*/transformations/dbt/*/profiles.yml")):
    body = path.read_text()
    if "POLARIS_" in body:
        errors.append(f"{path}: product dbt profiles must use OPENLAKEFORGE_CATALOG_* env vars, not POLARIS_*")
    if "seaweedfs-s3" in body:
        errors.append(f"{path}: product dbt profiles must not hardcode local storage endpoints")
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
    if re.search(r"^\s*database:\s*lakehouse_dev\s*$", body, re.MULTILINE):
        errors.append(f"{path}: source database must use OPENLAKEFORGE_CATALOG_WAREHOUSE")

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
