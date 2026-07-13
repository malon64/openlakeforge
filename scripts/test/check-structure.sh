#!/usr/bin/env bash
set -euo pipefail

required_paths=(
  "README.md"
  "Makefile"
  ".gitignore"
  ".github/workflows/checks.yml"
  "docs/architecture/README.md"
  "docs/architecture/overview.md"
  "docs/architecture/azure-aks-poc.md"
  "docs/architecture/aws-eks-poc.md"
  "docs/architecture/provider-contracts.md"
  "docs/technical-debt.md"
  "docs/testing/floe-openlineage-capture-test-plan.md"
  "docs/adr/README.md"
  "docs/adr/0001-v1-platform-baseline.md"
  "docs/adr/0002-local-object-storage-seaweedfs.md"
  "docs/adr/0003-local-dagster-project-code-runtime.md"
  "docs/adr/0010-provider-contract-first-cloud-readiness.md"
  "docs/adr/0011-iceberg-catalog-contract-allows-glue.md"
  "docs/adr/0012-contract-driven-provider-first-hardening.md"
  "docs/adr/0014-ops-artifact-bucket-and-domain-dagster-locations.md"
  "docs/adr/0015-aws-eks-managed-services-poc.md"
  "infra/README.md"
  "infra/terraform/README.md"
  "infra/terraform/environments/local/contracts.tf"
  "infra/terraform/environments/azure-poc/main.tf"
  "infra/terraform/environments/azure-poc/variables.tf"
  "infra/terraform/environments/azure-poc/outputs.tf"
  "infra/terraform/environments/azure-poc/contracts.tf"
  "infra/terraform/environments/aws-poc/main.tf"
  "infra/terraform/environments/aws-poc/variables.tf"
  "infra/terraform/environments/aws-poc/outputs.tf"
  "infra/terraform/environments/aws-poc/contracts.tf"
  "infra/terraform/foundations/local-kind/main.tf"
  "infra/terraform/foundations/local-kind/variables.tf"
  "infra/terraform/foundations/local-kind/outputs.tf"
  "infra/terraform/foundations/azure-aks/main.tf"
  "infra/terraform/foundations/azure-aks/variables.tf"
  "infra/terraform/foundations/azure-aks/outputs.tf"
  "infra/terraform/foundations/aws-eks/main.tf"
  "infra/terraform/foundations/aws-eks/variables.tf"
  "infra/terraform/foundations/aws-eks/outputs.tf"
  "infra/terraform/modules/storage/aws-s3/main.tf"
  "infra/terraform/modules/storage/aws-s3/variables.tf"
  "infra/terraform/modules/storage/aws-s3/outputs.tf"
  "infra/terraform/modules/storage/rds-postgresql/main.tf"
  "infra/terraform/modules/storage/rds-postgresql/variables.tf"
  "infra/terraform/modules/storage/rds-postgresql/outputs.tf"
  "infra/terraform/modules/catalog/aws-glue/main.tf"
  "infra/terraform/modules/catalog/aws-glue/variables.tf"
  "infra/terraform/modules/catalog/aws-glue/outputs.tf"
  "infra/helm/README.md"
  "infra/helm/values/local/dagster.yaml"
  "infra/helm/values/local/superset.yaml"
  "images/README.md"
  "images/project-code/README.md"
  "images/project-code/Dockerfile"
  "images/project-code/pyproject.toml"
  "images/superset/README.md"
  "images/superset/Dockerfile"
  "libs/README.md"
  "libs/__init__.py"
  "libs/bronze_csv.py"
  "libs/k8s_log_archive.py"
  "libs/openlakeforge_logging.py"
  "libs/dbt/__init__.py"
  "libs/dbt/openlakeforge_dbt/dbt_project.yml"
  "libs/dbt/openlakeforge_dbt/macros/generate_schema_name.sql"
  "libs/dbt/openlakeforge_dbt/macros/iceberg_table.sql"
  "libs/dbt/profiles/local.yml"
  "libs/dbt/profiles/azure.yml"
  "libs/dbt/profiles/aws.yml"
  "libs/dbt/render_profiles.py"
  "libs/floe/profiles/local-k8s.yml"
  "libs/floe/profiles/aws-eks.yml"
  "libs/product_dagster.py"
  "libs/s3_artifacts.py"
  "domains/README.md"
  "domains/sales/README.md"
  "domains/sales/__init__.py"
  "domains/sales/definitions.py"
  "domains/sales/domain.yaml"
  "domains/sales/contracts/floe/order_revenue.yml"
  "domains/sales/contracts/floe/customer_health.yml"
  "domains/sales/contracts/floe/manifests/order_revenue.manifest.json"
  "domains/sales/contracts/floe/manifests/customer_health.manifest.json"
  "domains/sales/examples/raw/order_revenue/orders.csv"
  "domains/sales/examples/raw/customer_health/accounts.csv"
  "domains/sales/extract/dlt/order_revenue.py"
  "domains/sales/extract/dlt/customer_health.py"
  "domains/sales/transformations/dbt/order_revenue/dbt_project.yml"
  "domains/sales/transformations/dbt/order_revenue/packages.yml"
  "domains/sales/transformations/dbt/customer_health/dbt_project.yml"
  "domains/sales/transformations/dbt/customer_health/packages.yml"
  "domains/sales/reports/superset/order_revenue/metadata.yaml"
  "domains/sales/reports/superset/order_revenue/charts/Daily_Net_Revenue_1.yaml"
  "domains/sales/reports/superset/customer_health/metadata.yaml"
  "domains/sales/reports/superset/customer_health/charts/Health_Score_by_Segment_1.yaml"
  "domains/sales/pipelines/dagster/order_revenue.py"
  "domains/sales/pipelines/dagster/customer_health.py"
  "domains/supply_chain/README.md"
  "domains/supply_chain/__init__.py"
  "domains/supply_chain/definitions.py"
  "domains/supply_chain/domain.yaml"
  "domains/supply_chain/contracts/floe/inventory_reliability.yml"
  "domains/supply_chain/contracts/floe/manifests/inventory_reliability.manifest.json"
  "domains/supply_chain/examples/raw/inventory_reliability/inventory_snapshots.csv"
  "domains/supply_chain/extract/dlt/inventory_reliability.py"
  "domains/supply_chain/transformations/dbt/inventory_reliability/dbt_project.yml"
  "domains/supply_chain/transformations/dbt/inventory_reliability/packages.yml"
  "domains/supply_chain/reports/superset/inventory_reliability/metadata.yaml"
  "domains/supply_chain/reports/superset/inventory_reliability/charts/Available_Inventory_by_Status_1.yaml"
  "domains/supply_chain/pipelines/dagster/inventory_reliability.py"
  "scripts/README.md"
  "scripts/lib/docker.sh"
  "scripts/test/check-structure.sh"
  "scripts/test/check-contracts.sh"
  "scripts/test/check-infra.sh"
  "scripts/test/check-project-code.sh"
  "scripts/test/check-dbt.sh"
  "scripts/local/stack/platform-up.sh"
  "scripts/local/stack/deploy-artifacts.sh"
  "scripts/local/stack/teardown.sh"
  "scripts/local/foundation/up.sh"
  "scripts/local/foundation/down.sh"
  "scripts/contracts/load-runtime-env.sh"
  "scripts/lib/common.sh"
  "scripts/lib/helm.sh"
  "scripts/lib/kube.sh"
  "scripts/lib/python.sh"
  "tools/olf/pyproject.toml"
  "tools/olf/uv.lock"
  "tools/olf/olf/cli.py"
  "tools/olf/olf/contracts.py"
  "tools/olf/olf/floe.py"
  "tools/olf/olf/k8s.py"
  "tools/olf/olf/e2e.py"
  "tools/olf/olf/s3.py"
  "tools/olf/olf/superset.py"
  "tools/olf/olf/openmetadata.py"
  "tools/olf/olf/polaris.py"
  "scripts/artifacts/floe-manifest.sh"
  "scripts/artifacts/dbt-parse.sh"
  "scripts/artifacts/olf.sh"
  "scripts/local/cluster/prefetch-images.sh"
  "scripts/local/images/build-project-code.sh"
  "scripts/local/images/load-project-code.sh"
  "scripts/local/images/build-superset.sh"
  "scripts/local/images/load-superset.sh"
  "scripts/azure/foundation/up.sh"
  "scripts/azure/foundation/down.sh"
  "scripts/azure/stack/platform-up.sh"
  "scripts/azure/stack/deploy-artifacts.sh"
  "scripts/azure/stack/teardown.sh"
  "scripts/azure/images/build-push-project-code.sh"
  "scripts/azure/images/build-push-superset.sh"
  "scripts/azure/test/e2e.sh"
  "scripts/aws/foundation/up.sh"
  "scripts/aws/foundation/down.sh"
  "scripts/aws/stack/platform-up.sh"
  "scripts/aws/stack/deploy-artifacts.sh"
  "scripts/aws/stack/teardown.sh"
  "scripts/aws/images/build-push-project-code.sh"
  "scripts/aws/images/build-push-superset.sh"
  "scripts/aws/test/e2e.sh"
)

forbidden_paths=(
  "infra/floe"
  "domains/sales/data_products"
  "domains/sales/governance/openmetadata"
  "domains/supply_chain/data_products"
  "domains/supply_chain/governance/openmetadata"
  "scripts/local/cluster/create.sh"
  "scripts/local/cluster/destroy.sh"
)

missing=0

for path in "${required_paths[@]}"; do
  if [[ ! -e "${path}" ]]; then
    printf 'Missing required path: %s\n' "${path}" >&2
    missing=1
  fi
done

for path in "${forbidden_paths[@]}"; do
  if [[ -e "${path}" ]]; then
    printf 'Forbidden legacy path still exists: %s\n' "${path}" >&2
    missing=1
  fi
done

if [[ "${missing}" -ne 0 ]]; then
  exit 1
fi

python3 <<'PY'
import re
import sys
from pathlib import Path

allowed_viz_types = {
    "echarts_timeseries_bar",
    "echarts_timeseries_line",
    "pie",
    "table",
}


def scalar(text, key):
    match = re.search(rf"^{re.escape(key)}:\s*(.+?)\s*$", text, re.MULTILINE)
    return match.group(1).strip() if match else None


def list_values(text, key):
    values = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not re.match(rf"^\s*{re.escape(key)}:\s*$", line):
            continue
        indent = len(line) - len(line.lstrip())
        for child in lines[index + 1 :]:
            if not child.strip():
                continue
            child_indent = len(child) - len(child.lstrip())
            if child_indent <= indent:
                break
            item = re.match(r"^\s*-\s*([A-Za-z_][A-Za-z0-9_]*)\s*$", child)
            if item:
                values.append(item.group(1))
    return values


datasets = {}
errors = []

for dataset_path in sorted(Path("domains").glob("*/reports/superset/*/datasets/*/*.yaml")):
    text = dataset_path.read_text()
    uuid = scalar(text, "uuid")
    table_name = scalar(text, "table_name")
    main_dttm_col = scalar(text, "main_dttm_col")
    columns = set(re.findall(r"^\s*-\s*column_name:\s*([A-Za-z_][A-Za-z0-9_]*)\s*$", text, re.MULTILINE))
    metrics = set(re.findall(r"^\s*-\s*metric_name:\s*([A-Za-z_][A-Za-z0-9_]*)\s*$", text, re.MULTILINE))
    if not uuid:
        errors.append(f"{dataset_path}: missing uuid")
        continue
    if uuid in datasets:
        errors.append(f"{dataset_path}: duplicate dataset uuid {uuid}")
    if main_dttm_col and main_dttm_col != "null" and main_dttm_col not in columns:
        errors.append(f"{dataset_path}: main_dttm_col {main_dttm_col} is not declared as a column")
    datasets[uuid] = {
        "path": dataset_path,
        "table_name": table_name,
        "columns": columns,
        "metrics": metrics,
    }

for chart_path in sorted(Path("domains").glob("*/reports/superset/*/charts/*.yaml")):
    text = chart_path.read_text()
    viz_type = scalar(text, "viz_type")
    dataset_uuid = scalar(text, "dataset_uuid")

    if viz_type not in allowed_viz_types:
        errors.append(f"{chart_path}: unsupported Superset viz_type {viz_type}")
    if dataset_uuid not in datasets:
        errors.append(f"{chart_path}: unknown dataset_uuid {dataset_uuid}")
        continue

    dataset = datasets[dataset_uuid]
    fields = []
    x_axis = scalar(text, "x_axis")
    if x_axis and x_axis != "null":
        fields.append(x_axis)
    fields.extend(list_values(text, "groupby"))
    fields.extend(list_values(text, "columns"))
    fields.extend(re.findall(r"^\s*subject:\s*([A-Za-z_][A-Za-z0-9_]*)\s*$", text, re.MULTILINE))

    for field in fields:
        if field not in dataset["columns"]:
            errors.append(
                f"{chart_path}: field {field} is not declared in "
                f"{dataset['path']} ({dataset['table_name']})"
            )

    metrics = list_values(text, "metrics")
    metric = scalar(text, "metric")
    if metric and metric != "null":
        metrics.append(metric)
    for metric_name in metrics:
        if metric_name not in dataset["metrics"]:
            errors.append(
                f"{chart_path}: metric {metric_name} is not declared in "
                f"{dataset['path']} ({dataset['table_name']})"
            )

if errors:
    for error in errors:
        print(error, file=sys.stderr)
    sys.exit(1)
PY

printf 'Repository structure is valid.\n'
