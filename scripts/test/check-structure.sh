#!/usr/bin/env bash
set -euo pipefail

required_paths=(
  "README.md"
  "Makefile"
  ".gitignore"
  ".github/workflows/checks.yml"
  "docs/architecture/README.md"
  "docs/architecture/overview.md"
  "docs/architecture/provider-contracts.md"
  "docs/adr/README.md"
  "docs/adr/0001-v1-platform-baseline.md"
  "docs/adr/0002-local-object-storage-seaweedfs.md"
  "docs/adr/0003-local-dagster-project-code-runtime.md"
  "docs/adr/0010-provider-contract-first-cloud-readiness.md"
  "docs/adr/0011-iceberg-catalog-contract-allows-glue.md"
  "infra/README.md"
  "infra/terraform/README.md"
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
  "domains/README.md"
  "domains/__init__.py"
  "domains/sales/README.md"
  "domains/sales/__init__.py"
  "domains/sales/domain.yaml"
  "domains/sales/governance/openmetadata/domain.yaml"
  "domains/sales/governance/openmetadata/data-products/sales_gold_marts.yaml"
  "domains/sales/examples/raw/sales.csv"
  "domains/sales/examples/raw/customers.csv"
  "domains/sales/examples/raw/products.csv"
  "domains/sales/extract/__init__.py"
  "domains/sales/extract/dlt/__init__.py"
  "domains/sales/extract/dlt/sales_poc.py"
  "domains/sales/contracts/floe/sales_poc.yml"
  "domains/sales/contracts/floe/profiles/local-k8s.yml"
  "domains/sales/contracts/floe/manifests/sales.manifest.json"
  "domains/sales/reports/superset/README.md"
  "domains/sales/reports/superset/metadata.yaml"
  "domains/sales/reports/superset/dashboards/Sales_Gold_Mart_Overview_1.yaml"
  "domains/sales/pipelines/__init__.py"
  "domains/sales/pipelines/dagster/__init__.py"
  "domains/sales/pipelines/dagster/definitions.py"
  "domains/sales/tests/test_sales_pipeline_definitions.py"
  "scripts/README.md"
  "scripts/test/check-structure.sh"
  "scripts/test/check-infra.sh"
  "scripts/test/check-project-code.sh"
  "scripts/test/check-dbt.sh"
  "scripts/local/stack/setup.sh"
  "scripts/local/stack/infra-up.sh"
  "scripts/local/stack/deploy-artifacts.sh"
  "scripts/local/stack/teardown.sh"
  "scripts/local/cluster/create.sh"
  "scripts/local/cluster/destroy.sh"
  "scripts/local/cluster/prefetch-images.sh"
  "scripts/local/artifacts/floe-manifest.sh"
  "scripts/local/artifacts/upload-floe-manifest.sh"
  "scripts/local/artifacts/dbt-parse.sh"
  "scripts/local/artifacts/openmetadata-metadata-deploy.sh"
  "scripts/local/artifacts/superset-reports-deploy.sh"
  "scripts/local/artifacts/superset-reports-export.sh"
  "scripts/local/images/build-project-code.sh"
  "scripts/local/images/load-project-code.sh"
  "scripts/local/images/build-superset.sh"
  "scripts/local/images/load-superset.sh"
)

missing=0

for path in "${required_paths[@]}"; do
  if [[ ! -e "${path}" ]]; then
    printf 'Missing required path: %s\n' "${path}" >&2
    missing=1
  fi
done

if [[ "${missing}" -ne 0 ]]; then
  exit 1
fi

printf 'Repository structure is valid.\n'
