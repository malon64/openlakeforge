"""Runtime invariants for descriptor-driven Dagster project definitions.

This module deliberately runs in the project-code dependency environment created
by ``olf check project-code``.  It is kept as Python so it remains testable and
does not depend on a checkout script or shell interpolation.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from hashlib import sha256
from importlib import import_module
from pathlib import Path


def _fail(message: str) -> None:
    raise RuntimeError(message)


def _asset_keys(definitions: object) -> list[tuple[str, ...]]:
    return [
        tuple(key.path)
        for asset_def in getattr(definitions, "assets", ())
        if hasattr(asset_def, "keys")
        for key in asset_def.keys
    ]


def _product_inputs(product: object) -> tuple[str, ...]:
    module = import_module(f"lakehouse_code.pipelines.dagster.{product.id}")
    name = f"{product.id.upper()}_SILVER_INPUTS"
    values = getattr(module, name, getattr(module, f"{product.id.upper()}_ENTITIES", None))
    if values is None:
        _fail(f"{module.__name__} does not define {name}")
    return tuple(values)


def validate(root: Path) -> None:
    """Validate product selection, Floe contracts, and immutable replay behavior."""
    from dagster import Definitions
    from dagster._core.workspace.autodiscovery import loadable_targets_from_python_module
    from floe_dagster.manifest import load_manifest
    from lakehouse_code.definitions import defs as merged_defs
    from libs import product_dagster as product_dagster_lib
    from openlakeforge_domain import load_lakehouse_inventory

    if os.environ.get("OPENLAKEFORGE_FLOE_MANIFEST_ACCESS_MODE", "").lower() != "remote":
        _fail("project-code check must load definitions in remote Floe manifest mode")
    inventory = load_lakehouse_inventory(root / "lakehouse_code")
    products = tuple(inventory.products)
    if not products:
        _fail("lakehouse inventory has no products")
    product_ids = {product.id for product in products}
    module_names = ["lakehouse_code.definitions", *(f"lakehouse_code.pipelines.dagster.{p}" for p in product_ids)]
    for module_name in module_names:
        targets = loadable_targets_from_python_module(module_name, str(root))
        if len(targets) != 1 or targets[0].attribute != "defs":
            _fail(f"{module_name} must expose exactly one defs target")

    product_defs = {
        product.id: import_module(f"lakehouse_code.pipelines.dagster.{product.id}").defs for product in products
    }
    merged_keys_list = _asset_keys(merged_defs)
    merged_keys = set(merged_keys_list)
    if len(merged_keys_list) != len(merged_keys):
        _fail("duplicate Dagster asset keys found")

    canonical_bronze = {(source.name, item.name) for source in inventory.sources for item in source.resources}
    canonical_silver = {(domain.name, item.name) for domain in inventory.domains for item in domain.silver_tables}
    canonical_gold = {(product.id, item.name) for product in products for item in product.gold_tables}
    all_product_keys: set[tuple[str, ...]] = set()

    for product in products:
        inputs = _product_inputs(product)
        expected_inputs = {item.name for item in inventory.resolved_silver_tables(product)}
        if set(inputs) != expected_inputs:
            _fail(f"{product.id} Silver inputs do not match lakehouse descriptor")
        domain = product.domain_name
        manifest_path = root / f"lakehouse_code/silver/{domain}/contracts/floe/manifests/{domain}.manifest.json"
        manifest = load_manifest(manifest_path)
        domain_tables = inventory.domain_for_product(product).silver_tables
        if {item.name for item in manifest.entities} != {item.name for item in domain_tables}:
            _fail(f"{product.id} Floe manifest entities do not match its domain Silver tables")
        expected_args = ["run", "--manifest", "{manifest_uri}", "--log-format", "json", "--quiet"]
        if manifest.execution.base_args not in (expected_args, [*expected_args, "--run-id", "{run_id}"]):
            _fail(f"{product.id} Floe manifest must use the runtime manifest_uri placeholder")
        if not str(manifest.report_base_uri).startswith(f"s3://openlakeforge-ops/floe/reports/{domain}"):
            _fail(f"{product.id} Floe reports must use the ops bucket")

        definition_keys = set(_asset_keys(product_defs[product.id]))
        all_product_keys.update(definition_keys)
        other_products = product_ids - {product.id}
        if not any(key[0] == product.id for key in definition_keys):
            _fail(f"{product.id} definitions did not load product assets")
        if any(key[0] in other_products for key in definition_keys):
            _fail(f"{product.id} definitions load another product's assets")

        job = merged_defs.resolve_job_def(product.job_name)
        selected = {tuple(key.path) for key in job.asset_layer.selected_asset_keys}
        expected_bronze = {(item.source, item.name) for item in inventory.bronze_resources_for_product(product)}
        expected_silver = {(domain, item) for item in inputs}
        expected_gold = {(product.id, item.name) for item in product.gold_tables}
        if selected & canonical_bronze != expected_bronze:
            _fail(f"{product.job_name} does not select exactly its Bronze inputs")
        if selected & canonical_silver != expected_silver:
            _fail(f"{product.job_name} does not select exactly its Silver inputs")
        if selected & canonical_gold != expected_gold:
            _fail(f"{product.job_name} does not select exactly its Gold assets")
        if not expected_bronze | expected_silver | expected_gold <= merged_keys:
            _fail(f"{product.id} selected assets are absent from merged definitions")

    if not all_product_keys <= merged_keys:
        _fail("merged definitions do not contain every product asset")
    _verify_immutable_manifest_replay(root, products[0], product_dagster_lib, load_manifest)
    _verify_shared_assets(inventory, merged_keys_list)
    Definitions.validate_loadable(merged_defs)
    merged_defs.get_repository_def().load_all_definitions()


def _verify_immutable_manifest_replay(root: Path, product: object, library: object, load_manifest: object) -> None:
    """Exercise the revision sidecar/digest guard used by cloud manifest replay."""
    domain = product.domain_name
    manifest_path = root / f"lakehouse_code/silver/{domain}/contracts/floe/manifests/{domain}.manifest.json"
    payload = json.loads(manifest_path.read_text())
    payload["execution"]["base_args"] = [
        "run", "--manifest", "{manifest_uri}", "--log-format", "json", "--quiet", "--run-id", "{run_id}",
    ]
    env_keys = (
        "OPENLAKEFORGE_CATALOG_TYPE",
        "OPENLAKEFORGE_CATALOG_PROVIDER",
        "OPENLAKEFORGE_FLOE_MANIFEST_CACHE_DIR",
        "OPENLAKEFORGE_FLOE_MANIFEST_REVISION_BUILT",
    )
    original_env = {key: os.environ.get(key) for key in env_keys}
    original_reader = library.read_text_uri
    artifact_key = f"floe/manifests/{domain}/{domain}.manifest.json"
    entries = {artifact_key: sha256(json.dumps(payload).encode()).hexdigest()}
    revision = library._aggregate_revision(entries)
    try:
        os.environ.update({
            "OPENLAKEFORGE_CATALOG_TYPE": "glue",
            "OPENLAKEFORGE_CATALOG_PROVIDER": "aws-glue",
            "OPENLAKEFORGE_FLOE_MANIFEST_CACHE_DIR": str(root / ".tmp/project-code-check-floe-manifests"),
            "OPENLAKEFORGE_FLOE_MANIFEST_REVISION_BUILT": revision,
        })
        spec = library.DomainDefinitionSpec(domain=domain, tables=())
        revision_uri = library._remote_manifest_uri(spec)
        sidecar_uri = revision_uri.rsplit("/floe/manifests/", 1)[0] + "/REVISION.json"
        sidecar = json.dumps({"revision": revision, "entries": entries})
        library.read_text_uri = lambda uri: sidecar if uri == sidecar_uri else json.dumps(payload)
        cached = library._manifest_path_for_dagster(spec)
        load_manifest(cached)
        library.read_text_uri = lambda uri: sidecar if uri == sidecar_uri else "tampered"
        try:
            library._manifest_path_for_dagster(spec)
        except library.ArtifactRevisionError:
            pass
        else:
            _fail("project-code accepted an immutable Floe manifest with the wrong digest")
    finally:
        library.read_text_uri = original_reader
        for key, value in original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _verify_shared_assets(inventory: object, asset_keys: list[tuple[str, ...]]) -> None:
    counts = Counter(
        (item.source, item.name)
        for product in inventory.products
        for item in inventory.bronze_resources_for_product(product)
    )
    counts.update(
        (item.domain, item.name)
        for product in inventory.products
        for item in inventory.resolved_silver_tables(product)
    )
    for key, count in counts.items():
        if count > 1 and asset_keys.count(key) != 1:
            _fail(f"shared asset {key[0]}/{key[1]} must have exactly one definition")


def main() -> None:
    try:
        validate(Path(sys.argv[1]).resolve())
    except RuntimeError as exc:
        raise SystemExit(f"ERROR: {exc}") from exc
    print("Merged and product Dagster definitions loaded.")


if __name__ == "__main__":
    main()
