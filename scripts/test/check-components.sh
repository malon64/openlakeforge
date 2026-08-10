#!/usr/bin/env bash
set -euo pipefail

catalog=release/component-catalog.yaml
[[ -f "$catalog" ]] || { echo "Missing $catalog" >&2; exit 1; }
grep -q '^apiVersion: openlakeforge.io/v1alpha1$' "$catalog"
grep -q '^kind: ComponentCatalog$' "$catalog"
grep -Eq '^  version: [0-9]+\.[0-9]+\.[0-9]+-alpha\.[0-9]+$' "$catalog"

if command -v terraform >/dev/null 2>&1; then
  while IFS= read -r lockfile; do
    directory="${lockfile%/.terraform.lock.hcl}"
    terraform -chdir="$directory" init -backend=false -input=false -lockfile=readonly >/dev/null
  done < <(find infra/terraform -name .terraform.lock.hcl -print | sort)
fi

# GitHub Action SHA-pinning and Dockerfile digest-pinning are validated by
# `olf release check` (tools/olf/olf/release.py), which also cross-checks
# both against release/component-catalog.yaml -- a single implementation
# instead of a duplicate that can disagree with it.
uv run --project tools/olf --locked olf release check

bad=0
while IFS= read -r match; do
  file="${match%%:*}"
  remainder="${match#*:}"
  line="${remainder#*:}"
  [[ "$line" == *"#"* ]] && continue
  if [[ "$line" =~ (python:3\.12-slim|apache/superset:6\.1\.0|postgres:16-alpine|chrislusf/seaweedfs:4\.23) ]] && [[ ! "$line" =~ @sha256:[0-9a-f]{64} ]]; then
    printf 'Unpinned release image in %s: %s\n' "$file" "$line" >&2
    bad=1
  fi
done < <(rg -n 'python:3\.12-slim|apache/superset:6\.1\.0|postgres:16-alpine|chrislusf/seaweedfs:4\.23' scripts images infra/terraform infra/helm --glob '!scripts/test/check-components.sh' --glob '!**/README.md')

while IFS= read -r match; do
  file="${match%%:*}"
  remainder="${match#*:}"
  line="${remainder#*:}"
  if [[ ! "$line" =~ @sha256:[0-9a-f]{64} ]]; then
    printf 'Unpinned Helm image value in %s: %s\n' "$file" "$line" >&2
    bad=1
  fi
done < <(rg -n '^[[:space:]]*(tag:|[[:alnum:]_]*Image:)' infra/helm/values --glob '*.yaml')

(( bad == 0 )) || exit 1
echo 'Component catalog and immutable input checks passed.'
