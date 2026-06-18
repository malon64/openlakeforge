#!/usr/bin/env bash
set -euo pipefail

select_python() {
  local candidate
  local -a candidates
  if [[ -n "${PYTHON_BIN:-}" ]]; then
    candidates=("${PYTHON_BIN}")
  else
    candidates=(python3.12 python3)
  fi

  for candidate in "${candidates[@]}"; do
    if ! command -v "${candidate}" &>/dev/null; then
      continue
    fi
    if "${candidate}" - <<'PY'
import sys
raise SystemExit(not (sys.version_info >= (3, 12) and sys.version_info < (3, 13)))
PY
    then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done

  printf "ERROR: check-project-code requires Python >=3.12,<3.13. Set PYTHON_BIN to a compatible interpreter.\n" >&2
  exit 1
}

PYTHON_BIN="$(select_python)"

CACHE_ROOT="${PROJECT_CODE_CHECK_CACHE_DIR:-.cache/project-code-check}"
python_tag="$("${PYTHON_BIN}" - <<'PY'
import sys
print(f"py{sys.version_info.major}{sys.version_info.minor}")
PY
)"
pyproject_hash="$("${PYTHON_BIN}" - <<'PY'
import hashlib
from pathlib import Path
print(hashlib.sha256(Path("images/project-code/pyproject.toml").read_bytes()).hexdigest()[:16])
PY
)"
site_dir="${CACHE_ROOT}/${python_tag}-${pyproject_hash}/site"
stamp_path="${CACHE_ROOT}/${python_tag}-${pyproject_hash}/.complete"

if [[ ! -f "${stamp_path}" ]]; then
  rm -rf "${CACHE_ROOT:?}/${python_tag}-${pyproject_hash}"
  mkdir -p "${site_dir}"

  dependencies=()
  while IFS= read -r dependency; do
    dependencies+=("${dependency}")
  done < <("${PYTHON_BIN}" - <<'PY'
import ast
from pathlib import Path

text = Path("images/project-code/pyproject.toml").read_text()
in_dependencies = False
dependencies = []
for line in text.splitlines():
    stripped = line.strip()
    if not in_dependencies:
        if stripped == "dependencies = [":
            in_dependencies = True
        continue
    if stripped == "]":
        break
    if stripped:
        dependencies.append(ast.literal_eval(stripped.rstrip(",")))

if not dependencies:
    raise SystemExit("images/project-code/pyproject.toml: missing project dependencies")

for dependency in dependencies:
    print(dependency)
PY
  )

  echo "==> Installing project-code dependencies into ${site_dir}"
  PYTHONDONTWRITEBYTECODE=1 "${PYTHON_BIN}" -m pip install \
    --disable-pip-version-check \
    --no-compile \
    --prefer-binary \
    --target "${site_dir}" \
    "${dependencies[@]}"
  touch "${stamp_path}"
else
  echo "==> Reusing project-code dependency cache ${site_dir}"
fi

echo "==> Loading aggregate Dagster product definitions"
PATH="${site_dir}/bin:${PATH}" PYTHONPATH="${site_dir}:${PWD}" "${PYTHON_BIN}" - <<'PY'
import os
from pathlib import Path

from dagster import AssetKey
from dagster._core.workspace.autodiscovery import loadable_targets_from_python_module
from floe_dagster.manifest import load_manifest

os.environ.setdefault("OPENLAKEFORGE_FLOE_MANIFEST_ACCESS_MODE", "remote")
os.environ.setdefault("OPENLAKEFORGE_FLOE_MANIFEST_BASE_URI", "s3://openlakeforge-code/floe")

from domains.definitions import defs
from domains.sales.extract.dlt.customer_health import CUSTOMER_HEALTH_ENTITIES
from domains.sales.extract.dlt.order_revenue import ORDER_REVENUE_ENTITIES
from domains.supply_chain.extract.dlt.inventory_reliability import (
    INVENTORY_RELIABILITY_ENTITIES,
)

PRODUCTS = [
    {
        "domain": "sales",
        "product": "order_revenue",
        "prefix": "sales_order_revenue",
        "job": "sales_order_revenue_pipeline",
        "manifest": Path("domains/sales/contracts/floe/manifests/order_revenue.manifest.json"),
        "entities": ORDER_REVENUE_ENTITIES,
        "gold": {
            "mart_order_revenue_by_day",
            "mart_order_revenue_by_channel",
            "mart_order_revenue_margin_by_product",
        },
    },
    {
        "domain": "sales",
        "product": "customer_health",
        "prefix": "sales_customer_health",
        "job": "sales_customer_health_pipeline",
        "manifest": Path("domains/sales/contracts/floe/manifests/customer_health.manifest.json"),
        "entities": CUSTOMER_HEALTH_ENTITIES,
        "gold": {
            "mart_customer_health_score",
            "mart_churn_risk_by_segment",
            "mart_support_sla_by_customer",
        },
    },
    {
        "domain": "supply_chain",
        "product": "inventory_reliability",
        "prefix": "supply_chain_inventory_reliability",
        "job": "supply_chain_inventory_reliability_pipeline",
        "manifest": Path(
            "domains/supply_chain/contracts/floe/manifests/inventory_reliability.manifest.json"
        ),
        "entities": INVENTORY_RELIABILITY_ENTITIES,
        "gold": {
            "mart_inventory_position",
            "mart_supplier_delivery_reliability",
            "mart_stockout_risk",
        },
    },
]

if os.environ["OPENLAKEFORGE_FLOE_MANIFEST_ACCESS_MODE"].strip().lower() != "remote":
    raise SystemExit("project-code check must load Dagster definitions in remote Floe manifest mode")

targets = loadable_targets_from_python_module("domains.definitions", ".")
if len(targets) != 1:
    raise SystemExit(f"domains.definitions should expose exactly one Dagster target, found {len(targets)}")
if targets[0].attribute != "defs":
    raise SystemExit(f"domains.definitions should expose defs, found {targets[0].attribute}")

asset_keys = {
    tuple(key.path)
    for asset_def in defs.assets
    if hasattr(asset_def, "keys")
    for key in asset_def.keys
}
source_asset_keys = {
    tuple(asset.key.path) for asset in defs.assets if not hasattr(asset, "keys")
}

for product in PRODUCTS:
    prefix = product["prefix"]
    env_key = prefix.upper()
    base_uri = os.environ["OPENLAKEFORGE_FLOE_MANIFEST_BASE_URI"].rstrip("/")
    remote_uri = os.environ.get(
        f"OPENLAKEFORGE_FLOE_MANIFEST_URI_{env_key}",
        f"{base_uri}/{product['domain']}/{product['product']}/{product['product']}.manifest.json",
    )
    if not remote_uri.startswith(("s3://", "gs://", "abfs://")):
        raise SystemExit(f"{prefix} remote Floe manifest URI is not a supported remote URI")

    manifest = load_manifest(product["manifest"])
    if manifest.execution.base_args != [
        "run",
        "--manifest",
        "{manifest_uri}",
        "--log-format",
        "json",
        "--quiet",
        "--run-id",
        "{run_id}",
    ]:
        raise SystemExit(f"{prefix} Floe manifest does not use the runtime manifest_uri placeholder")
    if manifest.execution.orchestration is None or manifest.execution.orchestration.strategy != "sequential":
        raise SystemExit(f"{prefix} Floe manifest should use sequential orchestration locally")
    if {entity.name for entity in manifest.entities} != set(product["entities"]):
        raise SystemExit(f"{prefix} Floe manifest entities do not match product entities")

    job = defs.resolve_job_def(product["job"])
    if job.name != product["job"]:
        raise SystemExit(f"missing Dagster job {product['job']}")
    if job.run_config["execution"]["config"]["multiprocess"]["max_concurrent"] != 1:
        raise SystemExit(f"{product['job']} did not inherit Floe orchestration concurrency")

    for entity in product["entities"]:
        if (prefix, f"{entity}_source") not in asset_keys:
            raise SystemExit(f"missing Bronze source asset for {prefix}/{entity}")
        if (prefix, entity) not in asset_keys:
            raise SystemExit(f"missing Floe Silver asset for {prefix}/{entity}")
        if (prefix, f"{entity}_source") in source_asset_keys:
            raise SystemExit(f"Floe registered a duplicate source asset for {prefix}/{entity}")

        matching_entities = [item for item in manifest.entities if item.name == entity]
        if not matching_entities:
            raise SystemExit(f"missing Floe manifest entity for {prefix}/{entity}")
        if matching_entities[0].group_name != prefix:
            raise SystemExit(f"Floe manifest entity {entity} is not in group {prefix}")
        if matching_entities[0].asset_key != [prefix, entity]:
            raise SystemExit(f"Floe manifest entity {entity} has wrong asset key")

    for asset_name in product["gold"]:
        if (prefix, asset_name) not in asset_keys:
            raise SystemExit(f"missing dbt Gold asset {prefix}/{asset_name}")

if len(asset_keys) != len(set(asset_keys)):
    raise SystemExit("duplicate Dagster asset keys found")

print("Aggregate product Dagster definitions loaded.")
PY
