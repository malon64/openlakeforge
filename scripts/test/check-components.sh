#!/usr/bin/env bash
set -euo pipefail

catalog=release/component-catalog.yaml
[[ -f "$catalog" ]] || { echo "Missing $catalog" >&2; exit 1; }
grep -q '^apiVersion: openlakeforge.io/v1alpha1$' "$catalog"
grep -q '^kind: ComponentCatalog$' "$catalog"
grep -Eq '^  version: [0-9]+\.[0-9]+\.[0-9]+-alpha\.[0-9]+$' "$catalog"
[[ -s images/project-code/requirements.lock ]] || { echo "Project-code lockfile missing" >&2; exit 1; }

if command -v terraform >/dev/null 2>&1; then
  while IFS= read -r lockfile; do
    directory="${lockfile%/.terraform.lock.hcl}"
    terraform -chdir="$directory" init -backend=false -input=false -lockfile=readonly >/dev/null
  done < <(find infra/terraform -name .terraform.lock.hcl -print | sort)
fi

bad=0
while IFS= read -r match; do
  file="${match%%:*}"
  remainder="${match#*:}"
  line="${remainder#*:}"
  if [[ "$line" =~ uses:[[:space:]]*[^@]+@([^[:space:]]+) ]]; then
    ref="${BASH_REMATCH[1]}"
  else
    ref=""
  fi
  if [[ ! "$ref" =~ ^[0-9a-f]{40}$ ]]; then
    printf 'Unpinned GitHub Action in %s: %s\n' "$file" "$line" >&2
    bad=1
  fi
done < <(rg -n '^[[:space:]]*uses:' .github/workflows)

while IFS= read -r match; do
  file="${match%%:*}"
  remainder="${match#*:}"
  line="${remainder#*:}"
  if [[ "$line" =~ ^[[:space:]]*FROM[[:space:]]+\$\{ ]]; then
    continue
  fi
  if [[ "$line" =~ ^[[:space:]]*FROM[[:space:]] ]] && [[ ! "$line" =~ @sha256:[0-9a-f]{64} ]]; then
    printf 'Unpinned container base in %s: %s\n' "$file" "$line" >&2
    bad=1
  fi
  if [[ "$line" =~ ^[[:space:]]*ARG[[:space:]]+[A-Z_]*IMAGE= ]] && [[ ! "$line" =~ @sha256:[0-9a-f]{64} ]]; then
    printf 'Unpinned container base argument in %s: %s\n' "$file" "$line" >&2
    bad=1
  fi
done < <(rg -n '^[[:space:]]*(FROM|ARG[[:space:]]+[A-Z_]*IMAGE=)' --glob 'Dockerfile*' .)

while IFS= read -r match; do
  file="${match%%:*}"
  remainder="${match#*:}"
  line="${remainder#*:}"
  [[ "$line" == *"#"* ]] && continue
  if [[ "$line" =~ (python:3\.12-slim|apache/superset:6\.1\.0|postgres:16-alpine|chrislusf/seaweedfs:4\.23) ]] && [[ ! "$line" =~ @sha256:[0-9a-f]{64} ]]; then
    printf 'Unpinned release image in %s: %s\n' "$file" "$line" >&2
    bad=1
  fi
done < <(rg -n 'python:3\.12-slim|apache/superset:6\.1\.0|postgres:16-alpine|chrislusf/seaweedfs:4\.23' scripts images infra/terraform --glob '!scripts/test/check-components.sh' --glob '!**/README.md')

(( bad == 0 )) || exit 1
echo 'Component catalog and immutable input checks passed.'
