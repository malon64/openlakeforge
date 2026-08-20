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
python_tag="$("${PYTHON_BIN}" -c 'import sys; print(f"py{sys.version_info.major}{sys.version_info.minor}")')"
pyproject_hash="$("${PYTHON_BIN}" -c 'import hashlib, pathlib; print(hashlib.sha256(pathlib.Path("images/project-code/pyproject.toml").read_bytes()).hexdigest()[:16])')"
domain_model_hash="$("${PYTHON_BIN}" -c 'import hashlib, pathlib; root=pathlib.Path("packages/domain-model"); digest=hashlib.sha256(); [digest.update(path.relative_to(root).as_posix().encode()+path.read_bytes()) for path in sorted(root.rglob("*")) if path.is_file()]; print(digest.hexdigest()[:16])')"
cache_key="${python_tag}-${pyproject_hash}-${domain_model_hash}"
site_dir="${CACHE_ROOT}/${cache_key}/site"
stamp_path="${CACHE_ROOT}/${cache_key}/.complete"

if [[ ! -f "${stamp_path}" ]]; then
  rm -rf "${CACHE_ROOT:?}/${cache_key}"
  mkdir -p "${site_dir}"

  dependencies=()
  while IFS= read -r dependency; do
    dependencies+=("${dependency}")
  done < <("${PYTHON_BIN}" -c '
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
    if stripped and not stripped.startswith("#"):
        dependencies.append(ast.literal_eval(stripped.rstrip(",")))

if not dependencies:
    raise SystemExit("images/project-code/pyproject.toml: missing project dependencies")

for dependency in dependencies:
    print(dependency)
')

  echo "==> Installing project-code dependencies into ${site_dir}"
  PYTHONDONTWRITEBYTECODE=1 "${PYTHON_BIN}" -m pip install \
    --disable-pip-version-check \
    --no-compile \
    --prefer-binary \
    --target "${site_dir}" \
    "${dependencies[@]}"
  uv pip install \
    --python "${PYTHON_BIN}" \
    --target "${site_dir}" \
    --no-deps \
    ./packages/domain-model
  touch "${stamp_path}"
else
  echo "==> Reusing project-code dependency cache ${site_dir}"
fi

echo "==> Loading domain Dagster product definitions"
PATH="${site_dir}/bin:${PATH}" PYTHONPATH="${site_dir}:${PWD}" "${PYTHON_BIN}" - <<'PY'
import json
import os
from hashlib import sha256
from pathlib import Path

from dagster import AssetKey
from dagster._core.workspace.autodiscovery import loadable_targets_from_python_module
from floe_dagster.manifest import load_manifest

os.environ.setdefault("OPENLAKEFORGE_FLOE_MANIFEST_ACCESS_MODE", "remote")
os.environ.setdefault("OPENLAKEFORGE_ARTIFACT_BUCKET_NAME", "openlakeforge-ops")
os.environ.setdefault("OPENLAKEFORGE_OPS_BUCKET_NAME", "openlakeforge-ops")
os.environ.setdefault("OPENLAKEFORGE_ARTIFACT_BASE_URI", "s3://openlakeforge-ops")
os.environ.setdefault("OPENLAKEFORGE_FLOE_MANIFEST_BASE_URI", "s3://openlakeforge-ops/floe/manifests")
os.environ.setdefault("OPENLAKEFORGE_FLOE_REPORT_BASE_URI", "s3://openlakeforge-ops/floe/reports")
os.environ.setdefault("OPENLAKEFORGE_LOG_BASE_URI", "s3://openlakeforge-ops/logs")
os.environ.setdefault("OPENLAKEFORGE_RUN_ARTIFACT_BASE_URI", "s3://openlakeforge-ops/run-artifacts")

from importlib import import_module

from lakehouse_code.definitions import defs as merged_defs
from dagster import Definitions
import libs.product_dagster as product_dagster_lib
from openlakeforge_domain import load_lakehouse_inventory


def _defined_entities(descriptor_product) -> tuple[str, ...]:
    """Read the *_ENTITIES tuple the product's Dagster pipeline module declares.

    This is a code fact, not descriptor data, so it stays a plain module
    lookup by naming convention rather than something lakehouse.yaml carries.
    """
    module = import_module(f"lakehouse_code.pipelines.dagster.{descriptor_product.id}")
    const_name = f"{descriptor_product.id.upper()}_ENTITIES"
    entities = getattr(module, const_name, None)
    if entities is None:
        raise SystemExit(f"{module.__name__} does not define {const_name}")
    return tuple(entities)


def _silver_inputs(descriptor_product) -> tuple[str, ...]:
    """Read the Silver inputs selected by a product job."""
    module = import_module(f"lakehouse_code.pipelines.dagster.{descriptor_product.id}")
    const_name = f"{descriptor_product.id.upper()}_SILVER_INPUTS"
    return tuple(getattr(module, const_name, _defined_entities(descriptor_product)))


def _source_entities(source_name: str) -> tuple[str, ...]:
    """Read the *_ENTITIES tuple the product's Bronze source loader declares.

    Bronze loaders are source-scoped (one module per source), so a product may
    only reference resources its source loader knows how to ingest.
    """
    module = import_module(f"lakehouse_code.bronze.{source_name}.dlt.{source_name}")
    const_name = f"{source_name.upper()}_ENTITIES"
    entities = getattr(module, const_name, None)
    if entities is None:
        raise SystemExit(f"{module.__name__} does not define {const_name}")
    return tuple(entities)


INVENTORY = load_lakehouse_inventory("lakehouse_code")
PRODUCTS = []
for _descriptor_product in INVENTORY.products:
    _entities = _defined_entities(_descriptor_product)
    _inputs = _silver_inputs(_descriptor_product)
    bronze_resources = INVENTORY.bronze_resources_for_product(_descriptor_product)
    bronze_names = {table.name for table in bronze_resources}
    if bronze_names != set(_inputs):
        raise SystemExit(
            f"lakehouse_code/lakehouse.yaml: product {_descriptor_product.id!r} bronze names "
            f"{sorted(bronze_names)} do not match pipeline Silver inputs {sorted(_inputs)}"
        )
    for source_name in {table.source for table in bronze_resources}:
        source_resources = {table.name for table in bronze_resources if table.source == source_name}
        missing_in_source = source_resources - set(_source_entities(source_name))
        if missing_in_source:
            raise SystemExit(
                f"lakehouse_code/lakehouse.yaml: product {_descriptor_product.id!r} Bronze resources "
                f"{sorted(missing_in_source)} are not declared by source loader {source_name!r}"
            )
    PRODUCTS.append(
        {
            "domain": _descriptor_product.domain_name,
            "product": _descriptor_product.id,
            "prefix": _descriptor_product.id,
            "job": _descriptor_product.job_name,
            "bronze_inputs": {(table.source, table.name) for table in bronze_resources},
            "manifest": Path(
                f"lakehouse_code/silver/{_descriptor_product.domain_name}/contracts/floe/manifests/"
                f"{_descriptor_product.domain_name}.manifest.json"
            ),
            "entities": _entities,
            "silver_inputs": _inputs,
            "domain_entities": {
                table.name for table in INVENTORY.domain_for_product(_descriptor_product).silver_tables
            },
            "gold": {table.name for table in _descriptor_product.gold_tables},
        }
    )

if os.environ["OPENLAKEFORGE_FLOE_MANIFEST_ACCESS_MODE"].strip().lower() != "remote":
    raise SystemExit("project-code check must load Dagster definitions in remote Floe manifest mode")

discovered_products = sorted({product["product"] for product in PRODUCTS})
product_definitions_modules = [f"lakehouse_code.pipelines.dagster.{product}" for product in discovered_products]
for module_name in ["lakehouse_code.definitions", *product_definitions_modules]:
    module_targets = loadable_targets_from_python_module(module_name, ".")
    if len(module_targets) != 1 or module_targets[0].attribute != "defs":
        raise SystemExit(f"{module_name} should expose exactly one defs target")

product_defs = {
    product: import_module(f"lakehouse_code.pipelines.dagster.{product}").defs for product in discovered_products
}
product_asset_key_list = [
    tuple(key.path)
    for definitions in product_defs.values()
    for asset_def in definitions.assets
    if hasattr(asset_def, "keys")
    for key in asset_def.keys
]
product_asset_keys_all = set(product_asset_key_list)
merged_asset_keys = {
    tuple(key.path)
    for asset_def in merged_defs.assets
    if hasattr(asset_def, "keys")
    for key in asset_def.keys
}
asset_key_list = [
    tuple(key.path)
    for asset_def in merged_defs.assets
    if hasattr(asset_def, "keys")
    for key in asset_def.keys
]
asset_keys = set(asset_key_list)
source_multi_assets = [
    asset_def
    for asset_def in merged_defs.assets
    if any(tuple(key.path)[0] in INVENTORY.source_names for key in getattr(asset_def, "keys", ()))
]
if len(source_multi_assets) != len(INVENTORY.sources):
    raise SystemExit("each Source must have exactly one executable Bronze multi-asset")
for source_asset in source_multi_assets:
    if not source_asset.can_subset:
        raise SystemExit("Source Bronze multi-assets must support product-specific subsets")
    required_outputs = [output.name for output in source_asset.op.output_defs if output.is_required]
    if required_outputs:
        raise SystemExit(
            "subsettable Source Bronze multi-assets must have optional outputs; "
            f"required outputs: {required_outputs}"
        )
product_asset_keys = {
    product: {
        tuple(key.path)
        for asset_def in definitions.assets
        if hasattr(asset_def, "keys")
        for key in asset_def.keys
    }
    for product, definitions in product_defs.items()
}

for product in PRODUCTS:
    prefix = product["prefix"]
    env_key = product["domain"].upper()
    base_uri = os.environ["OPENLAKEFORGE_FLOE_MANIFEST_BASE_URI"].rstrip("/")
    remote_uri = os.environ.get(
        f"OPENLAKEFORGE_FLOE_MANIFEST_URI_{env_key}",
        f"{base_uri}/{product['domain']}/{product['domain']}.manifest.json",
    )
    if not remote_uri.startswith(("s3://", "gs://", "abfs://")):
        raise SystemExit(f"{prefix} remote Floe manifest URI is not a supported remote URI")
    if "/floe/manifests/" not in remote_uri or "openlakeforge-ops" not in remote_uri:
        raise SystemExit(f"{prefix} remote Floe manifest URI must use the ops manifest prefix")

    manifest = load_manifest(product["manifest"])
    if not str(getattr(manifest, "report_base_uri", "")).startswith(
        f"s3://openlakeforge-ops/floe/reports/{product['domain']}"
    ):
        raise SystemExit(f"{prefix} Floe manifest must write reports to the ops bucket")
    expected_base_args = [
        "run",
        "--manifest",
        "{manifest_uri}",
        "--log-format",
        "json",
        "--quiet",
    ]
    expected_base_args_with_run_id = expected_base_args + ["--run-id", "{run_id}"]
    if manifest.execution.base_args not in [expected_base_args, expected_base_args_with_run_id]:
        raise SystemExit(f"{prefix} Floe manifest does not use the runtime manifest_uri placeholder")
    if manifest.execution.orchestration is None or manifest.execution.orchestration.strategy != "sequential":
        raise SystemExit(f"{prefix} Floe manifest should use sequential orchestration locally")
    if {entity.name for entity in manifest.entities} != product["domain_entities"]:
        raise SystemExit(f"{prefix} Floe manifest entities do not match domain Silver tables")

    job = merged_defs.resolve_job_def(product["job"])
    if job.name != product["job"]:
        raise SystemExit(f"missing Dagster job {product['job']}")
    if job.run_config["execution"]["config"]["multiprocess"]["max_concurrent"] != 1:
        raise SystemExit(f"{product['job']} did not inherit Floe orchestration concurrency")

    for source, resource in product["bronze_inputs"]:
        if (source, resource) not in asset_keys:
            raise SystemExit(f"missing Bronze source asset for {source}/{resource}")
    for entity in product["silver_inputs"]:
        if (product["domain"], entity) not in asset_keys:
            raise SystemExit(f"missing Floe Silver asset for {product['domain']}/{entity}")

    for entity in product["entities"]:

        matching_entities = [item for item in manifest.entities if item.name == entity]
        if not matching_entities:
            raise SystemExit(f"missing Floe manifest entity for {prefix}/{entity}")
        if matching_entities[0].group_name != product["domain"]:
            raise SystemExit(f"Floe manifest entity {entity} is not in group {product['domain']}")
        if matching_entities[0].asset_key != [product["domain"], entity]:
            raise SystemExit(f"Floe manifest entity {entity} has wrong asset key")

    for asset_name in product["gold"]:
        if (prefix, asset_name) not in asset_keys:
            raise SystemExit(f"missing dbt Gold asset {prefix}/{asset_name}")

sample_product = PRODUCTS[0]
remote_payload = json.loads(sample_product["manifest"].read_text())
remote_payload["execution"]["base_args"] = [
    "run",
    "--manifest",
    "{manifest_uri}",
    "--log-format",
    "json",
    "--quiet",
    "--run-id",
    "{run_id}",
]
remote_payload["execution"]["per_entity_args"] = ["--entities", "{entity_name}"]

previous_env = {
    key: os.environ.get(key)
    for key in [
        "OPENLAKEFORGE_CATALOG_TYPE",
        "OPENLAKEFORGE_CATALOG_PROVIDER",
        "OPENLAKEFORGE_FLOE_MANIFEST_CACHE_DIR",
        "OPENLAKEFORGE_FLOE_MANIFEST_REVISION_BUILT",
    ]
}
previous_reader = product_dagster_lib.read_text_uri
os.environ["OPENLAKEFORGE_CATALOG_TYPE"] = "glue"
os.environ["OPENLAKEFORGE_CATALOG_PROVIDER"] = "aws-glue"
os.environ["OPENLAKEFORGE_FLOE_MANIFEST_CACHE_DIR"] = ".tmp/project-code-check-floe-manifests"
product_dagster_lib.read_text_uri = lambda uri: json.dumps(remote_payload)
try:
    sample_spec = product_dagster_lib.DomainDefinitionSpec(
        domain=sample_product["domain"],
        tables=(),
    )
    cached_manifest_path = product_dagster_lib._manifest_path_for_dagster(sample_spec)
    cached_manifest = load_manifest(cached_manifest_path)
    if cached_manifest.execution.base_args[:3] != ["run", "--manifest", "{manifest_uri}"]:
        raise SystemExit("AWS remote Floe manifest was not used for Dagster manifest replay args")

    artifact_key = (
        f"floe/manifests/{sample_product['domain']}/{sample_product['domain']}.manifest.json"
    )
    entries = {artifact_key: sha256(json.dumps(remote_payload).encode()).hexdigest()}
    revision = product_dagster_lib._aggregate_revision(entries)
    os.environ["OPENLAKEFORGE_FLOE_MANIFEST_REVISION_BUILT"] = revision
    revision_uri = product_dagster_lib._remote_manifest_uri(sample_spec)
    expected_revision_uri = (
        f"s3://openlakeforge-ops/floe/revisions/sha256/{revision.removeprefix('sha256:')}/"
        f"{artifact_key}"
    )
    if revision_uri != expected_revision_uri:
        raise SystemExit("project-code image did not select its immutable Floe manifest revision")

    sidecar_uri = expected_revision_uri.rsplit("/floe/manifests/", 1)[0] + "/REVISION.json"
    product_dagster_lib.read_text_uri = lambda uri: (
        json.dumps({"revision": revision, "entries": entries})
        if uri == sidecar_uri
        else json.dumps(remote_payload)
    )
    cached_revision_path = product_dagster_lib._manifest_path_for_dagster(sample_spec)
    if Path(cached_revision_path).read_text() != json.dumps(remote_payload):
        raise SystemExit("project-code image did not cache its verified immutable Floe manifest")

    product_dagster_lib.read_text_uri = lambda uri: (
        json.dumps({"revision": revision, "entries": entries})
        if uri == sidecar_uri
        else "tampered"
    )
    try:
        product_dagster_lib._manifest_path_for_dagster(sample_spec)
    except product_dagster_lib.ArtifactRevisionError:
        pass
    else:
        raise SystemExit("project-code image accepted an immutable Floe manifest with the wrong digest")
finally:
    product_dagster_lib.read_text_uri = previous_reader
    for key, value in previous_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

for product in PRODUCTS:
    own_prefix = product["prefix"]
    foreign_prefixes = {other["prefix"] for other in PRODUCTS if other["prefix"] != own_prefix}
    own_asset_keys = product_asset_keys[product["product"]]
    if not any(key[0] == own_prefix for key in own_asset_keys):
        raise SystemExit(f"{product['product']} product definitions did not load its own product assets")
    if any(key[0] in foreign_prefixes for key in own_asset_keys):
        raise SystemExit(f"{product['product']} product definitions must not load other products' assets")

if len(asset_key_list) != len(asset_keys):
    raise SystemExit("duplicate Dagster asset keys found")
if not product_asset_keys_all.issubset(merged_asset_keys):
    raise SystemExit("merged Dagster definitions do not contain every product Gold asset")

for shared_key in (("crm", "accounts"), ("sales", "accounts")):
    if asset_key_list.count(shared_key) != 1:
        raise SystemExit(f"shared asset {shared_key[0]}/{shared_key[1]} must have exactly one definition")

legacy_keys = {
    key for key in asset_keys if key[0] in discovered_products and (key[1].endswith("_source") or key[1] in {
        table.name for product in INVENTORY.products for table in INVENTORY.resolved_silver_tables(product)
    })
}
if legacy_keys:
    raise SystemExit(f"old product-scoped Bronze/Silver asset keys remain: {sorted(legacy_keys)}")

# Dagster's gRPC server eagerly resolves every asset job.  Keeping this check
# eager is essential: lazily resolving only the named jobs previously let
# conflicting shared Silver definitions reach the deployment smoke test.
Definitions.validate_loadable(merged_defs)
merged_defs.get_repository_def().load_all_definitions()

print("Merged and product Dagster definitions loaded.")
PY
