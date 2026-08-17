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

from domains.definitions import defs as merged_defs
import libs.product_dagster as product_dagster_lib
from openlakeforge_domain import load_domain_inventory


def _dlt_entities(descriptor_product) -> tuple[str, ...]:
    """Read the *_ENTITIES tuple the product's dlt loader declares.

    This is a code fact, not descriptor data, so it stays a plain module
    lookup by naming convention rather than something domain.yaml carries.
    """
    module = import_module(f"domains.{descriptor_product.domain_name}.extract.dlt.{descriptor_product.id}")
    const_name = f"{descriptor_product.id.upper()}_ENTITIES"
    entities = getattr(module, const_name, None)
    if entities is None:
        raise SystemExit(f"{module.__name__} does not define {const_name}")
    return tuple(entities)


PRODUCTS = []
for _descriptor_product in load_domain_inventory("domains").products:
    _entities = _dlt_entities(_descriptor_product)
    bronze_names = {table.name for table in _descriptor_product.bronze_tables}
    if bronze_names != set(_entities):
        raise SystemExit(
            f"{_descriptor_product.domain_name}/domain.yaml: product {_descriptor_product.id!r} bronze names "
            f"{sorted(bronze_names)} do not match dlt entities {sorted(_entities)}"
        )
    PRODUCTS.append(
        {
            "domain": _descriptor_product.domain_name,
            "product": _descriptor_product.id,
            "prefix": _descriptor_product.asset_prefix,
            "job": _descriptor_product.job_name,
            "manifest": Path(
                f"domains/{_descriptor_product.domain_name}/contracts/floe/manifests/"
                f"{_descriptor_product.id}.manifest.json"
            ),
            "entities": _entities,
            "gold": {table.name for table in _descriptor_product.gold_tables},
        }
    )

if os.environ["OPENLAKEFORGE_FLOE_MANIFEST_ACCESS_MODE"].strip().lower() != "remote":
    raise SystemExit("project-code check must load Dagster definitions in remote Floe manifest mode")

discovered_domains = sorted({product["domain"] for product in PRODUCTS})
domain_product_prefixes = {
    domain_name: {product["prefix"] for product in PRODUCTS if product["domain"] == domain_name}
    for domain_name in discovered_domains
}

domain_definitions_modules = [f"domains.{domain_name}.definitions" for domain_name in discovered_domains]
for module_name in ["domains.definitions", *domain_definitions_modules]:
    module_targets = loadable_targets_from_python_module(module_name, ".")
    if len(module_targets) != 1 or module_targets[0].attribute != "defs":
        raise SystemExit(f"{module_name} should expose exactly one defs target")

domain_defs = {
    domain_name: import_module(f"domains.{domain_name}.definitions").defs for domain_name in discovered_domains
}
asset_key_list = [
    tuple(key.path)
    for definitions in domain_defs.values()
    for asset_def in definitions.assets
    if hasattr(asset_def, "keys")
    for key in asset_def.keys
]
asset_keys = set(asset_key_list)
merged_asset_keys = {
    tuple(key.path)
    for asset_def in merged_defs.assets
    if hasattr(asset_def, "keys")
    for key in asset_def.keys
}
source_asset_keys = {
    tuple(asset.key.path)
    for definitions in domain_defs.values()
    for asset in definitions.assets
    if not hasattr(asset, "keys")
}
domain_asset_keys = {
    domain_name: {
        tuple(key.path)
        for asset_def in definitions.assets
        if hasattr(asset_def, "keys")
        for key in asset_def.keys
    }
    for domain_name, definitions in domain_defs.items()
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
    if "/floe/manifests/" not in remote_uri or "openlakeforge-ops" not in remote_uri:
        raise SystemExit(f"{prefix} remote Floe manifest URI must use the ops manifest prefix")

    manifest = load_manifest(product["manifest"])
    if not str(getattr(manifest, "report_base_uri", "")).startswith(
        f"s3://openlakeforge-ops/floe/reports/{product['domain']}/{product['product']}"
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
    if {entity.name for entity in manifest.entities} != set(product["entities"]):
        raise SystemExit(f"{prefix} Floe manifest entities do not match product entities")

    job = merged_defs.resolve_job_def(product["job"])
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
    sample_spec = product_dagster_lib.ProductDefinitionSpec(
        domain=sample_product["domain"],
        product=sample_product["product"],
        asset_prefix=sample_product["prefix"],
        entities=tuple(sample_product["entities"]),
        gold_assets=tuple(sample_product["gold"]),
        domain_dir=Path("domains") / sample_product["domain"],
        bronze_loader=lambda: {},
    )
    cached_manifest_path = product_dagster_lib._manifest_path_for_dagster(sample_spec)
    cached_manifest = load_manifest(cached_manifest_path)
    if cached_manifest.execution.base_args[:3] != ["run", "--manifest", "{manifest_uri}"]:
        raise SystemExit("AWS remote Floe manifest was not used for Dagster manifest replay args")

    artifact_key = (
        f"floe/manifests/{sample_product['domain']}/{sample_product['product']}/"
        f"{sample_product['product']}.manifest.json"
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

for domain_name, own_asset_keys in domain_asset_keys.items():
    own_prefixes = domain_product_prefixes[domain_name]
    foreign_prefixes = {
        prefix
        for other_domain, prefixes in domain_product_prefixes.items()
        if other_domain != domain_name
        for prefix in prefixes
    }
    if not any(key[0] in own_prefixes for key in own_asset_keys):
        raise SystemExit(f"{domain_name} domain definitions did not load its own product assets")
    if any(key[0] in foreign_prefixes for key in own_asset_keys):
        raise SystemExit(f"{domain_name} domain definitions must not load other domains' assets")

if len(asset_key_list) != len(asset_keys):
    raise SystemExit("duplicate Dagster asset keys found")
if merged_asset_keys != asset_keys:
    raise SystemExit("merged Dagster definitions do not match all domain asset keys")

print("Merged and domain product Dagster definitions loaded.")
PY
