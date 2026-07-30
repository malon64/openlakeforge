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
  "libs/dbt/profiles/local.yml"
  "libs/dbt/profiles/azure.yml"
  "libs/dbt/profiles/aws.yml"
  "libs/dbt/render_profiles.py"
  "libs/floe/profiles/local-k8s.yml"
  "libs/floe/profiles/aws-eks.yml"
  "libs/product_dagster.py"
  "libs/s3_artifacts.py"
  "domains/README.md"
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
  "scripts/aws/foundation/up.sh"
  "scripts/aws/foundation/down.sh"
  "scripts/aws/stack/platform-up.sh"
  "scripts/aws/stack/deploy-artifacts.sh"
  "scripts/aws/stack/teardown.sh"
  "scripts/aws/images/build-push-project-code.sh"
  "scripts/aws/images/build-push-superset.sh"
)

forbidden_paths=(
  "infra/floe"
  "scripts/local/cluster/create.sh"
  "scripts/local/cluster/destroy.sh"
)

# Per-domain and per-product paths are derived from the domain descriptors, not
# listed here, so onboarding a product needs no change to this file. The
# derivation needs PyYAML, so it runs inside the olf project environment; a
# failure here is fatal rather than silently checking fewer paths.
derive_descriptor_paths() {
  uv run --quiet --project tools/olf python - <<'PY'
from olf.descriptors import discover_domains

for domain in discover_domains("."):
    base = f"domains/{domain.name}"
    for suffix in ("README.md", "__init__.py", "definitions.py", "domain.yaml"):
        print(f"required\t{base}/{suffix}")
    print(f"forbidden\t{base}/data_products")
    print(f"forbidden\t{base}/governance/openmetadata")
    for product in domain.products:
        # contracts/floe/manifests/<product>.manifest.json is deliberately not
        # required: it is a generated build artifact produced by
        # `make floe-manifest`, not a source file an author writes.
        print(f"required\t{base}/contracts/floe/{product.id}.yml")
        print(f"required\t{base}/extract/dlt/{product.id}.py")
        print(f"required\t{base}/pipelines/dagster/{product.id}.py")
        print(f"required\t{base}/transformations/dbt/{product.id}/dbt_project.yml")
        print(f"required\t{base}/transformations/dbt/{product.id}/packages.yml")
        print(f"required\t{base}/reports/superset/{product.id}/metadata.yaml")
PY
}

if ! descriptor_paths="$(derive_descriptor_paths)"; then
  echo "Failed to derive per-product paths from domain descriptors." >&2
  exit 1
fi
if [[ -z "${descriptor_paths}" ]]; then
  echo "Domain descriptor discovery produced no paths; expected at least one product." >&2
  exit 1
fi

while IFS=$'\t' read -r kind path; do
  [[ -z "${path}" ]] && continue
  case "${kind}" in
    required) required_paths+=("${path}") ;;
    forbidden) forbidden_paths+=("${path}") ;;
  esac
done <<< "${descriptor_paths}"

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
