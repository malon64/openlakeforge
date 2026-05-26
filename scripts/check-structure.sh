#!/usr/bin/env bash
set -euo pipefail

required_paths=(
  "README.md"
  "Makefile"
  ".gitignore"
  ".github/workflows/checks.yml"
  "docs/architecture/README.md"
  "docs/architecture/overview.md"
  "docs/adr/README.md"
  "docs/adr/0001-v1-platform-baseline.md"
  "docs/adr/0002-local-object-storage-seaweedfs.md"
  "docs/adr/0003-local-dagster-project-code-runtime.md"
  "infra/README.md"
  "infra/terraform/README.md"
  "infra/helm/README.md"
  "infra/helm/values/local/dagster.yaml"
  "images/README.md"
  "images/project-code/README.md"
  "images/project-code/Dockerfile"
  "images/project-code/pyproject.toml"
  "libs/README.md"
  "libs/__init__.py"
  "domains/README.md"
  "domains/__init__.py"
  "domains/sales/README.md"
  "domains/sales/__init__.py"
  "domains/sales/domain.yaml"
  "domains/sales/examples/raw/sales.csv"
  "domains/sales/examples/raw/customers.csv"
  "domains/sales/examples/raw/products.csv"
  "domains/sales/extract/__init__.py"
  "domains/sales/extract/dlt/__init__.py"
  "domains/sales/extract/dlt/sales_poc.py"
  "domains/sales/contracts/floe/sales_poc.yml"
  "domains/sales/contracts/floe/profiles/local-k8s.yml"
  "domains/sales/contracts/floe/manifests/sales.manifest.json"
  "domains/sales/pipelines/__init__.py"
  "domains/sales/pipelines/dagster/__init__.py"
  "domains/sales/pipelines/dagster/definitions.py"
  "domains/sales/tests/test_sales_pipeline_definitions.py"
  "scripts/README.md"
  "scripts/check-structure.sh"
  "scripts/check-infra.sh"
  "scripts/check-project-code.sh"
  "scripts/local/floe-manifest.sh"
  "scripts/local/build-project-code-image.sh"
  "scripts/local/load-project-code-image.sh"
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
