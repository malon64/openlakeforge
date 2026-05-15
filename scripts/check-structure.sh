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
  "infra/README.md"
  "infra/terraform/README.md"
  "infra/helm/README.md"
  "images/README.md"
  "images/project-code/README.md"
  "libs/README.md"
  "domains/README.md"
  "domains/sales/README.md"
  "domains/sales/domain.yaml"
  "domains/sales/examples/raw/.gitkeep"
  "domains/sales/ingestion/dlt/.gitkeep"
  "domains/sales/contracts/floe/.gitkeep"
  "domains/sales/transformations/dbt/.gitkeep"
  "domains/sales/orchestration/dagster/.gitkeep"
  "domains/sales/tests/.gitkeep"
  "scripts/README.md"
  "scripts/check-structure.sh"
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

printf 'Iteration 0 repository structure is valid.\n'
