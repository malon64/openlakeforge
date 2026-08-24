"""Verify-then-commit for a `ScaffoldPlan`.

Nothing is written to the real repository until the plan has been proven
valid in an isolated temp tree: the canonical descriptor model plus both
JSON Schemas, exactly as `olf contracts check` validates the committed
descriptors. On any failure, zero files are written and `lakehouse.yaml` is
left byte-identical.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import yaml
from openlakeforge_domain import LakehouseDescriptorError, LakehouseInventory, load_lakehouse_inventory

from olf.contracts_check import descriptor_schema_errors
from olf.scaffold._shared import ScaffoldError, ScaffoldFile, ScaffoldPlan

_SOURCE_YAML_SUFFIX = "source.yaml"
_BRONZE_PREFIX = "lakehouse_code/bronze/"
_YAML_SUFFIXES = (".yaml", ".yml")


def check_no_existing_targets(repo_root: Path, files: tuple[ScaffoldFile, ...]) -> None:
    existing = sorted(f.relative_path for f in files if (repo_root / f.relative_path).exists())
    if existing:
        raise ScaffoldError("refusing to overwrite existing file(s): " + ", ".join(existing))


def _check_yaml_well_formed(files: tuple[ScaffoldFile, ...]) -> None:
    """Every generated/edited YAML file must at least parse. `lakehouse.yaml`
    and `source.yaml` are already covered (more strictly) by the canonical
    model and JSON Schema; this catches malformed YAML in the rest -- Floe
    contracts, Superset bundles -- that those don't touch."""
    for scaffold_file in files:
        if not scaffold_file.relative_path.endswith(_YAML_SUFFIXES):
            continue
        try:
            yaml.safe_load(scaffold_file.content)
        except yaml.YAMLError as exc:
            raise ScaffoldError(f"{scaffold_file.relative_path}: generated content is not valid YAML: {exc}") from exc


def _check_asset_key_collisions(inventory: LakehouseInventory) -> None:
    """Dagster's `lakehouse_code/definitions.py` merges every source's Bronze
    assets (keyed `[source, resource]`), every domain's Silver assets (keyed
    `[domain, silver_table]`), and every product's Gold assets (keyed
    `[product, gold_table]`) into one code location. Two of those three key
    families colliding -- e.g. a domain named after an existing source,
    consuming a table named after one of that source's resources -- makes
    two distinct assets claim the same key, and the code location fails to
    load. The canonical model has no opinion on this (asset keys aren't part
    of the descriptor), so the scaffold checks it directly.
    """
    owners: dict[tuple[str, str], str] = {}
    collisions: list[str] = []

    def register(key: tuple[str, str], owner: str) -> None:
        existing = owners.get(key)
        if existing is not None and existing != owner:
            collisions.append(f"asset key {key!r} is claimed by both {existing} and {owner}")
        else:
            owners[key] = owner

    for source in inventory.sources:
        for resource in source.resources:
            register((source.name, resource.name), f"Bronze resource {source.name}/{resource.name}")
    for domain in inventory.domains:
        for table in domain.silver_tables:
            register((domain.name, table.name), f"Silver table {domain.name}/{table.name}")
    for product in inventory.products:
        for table in product.gold_tables:
            register((product.id, table.name), f"Gold table {product.id}/{table.name}")

    if collisions:
        raise ScaffoldError("; ".join(collisions))


def _verify(repo_root: Path, plan: ScaffoldPlan) -> None:
    _check_yaml_well_formed(plan.files)
    _check_yaml_well_formed(plan.edits)

    lakehouse_src = repo_root / "lakehouse_code"
    with tempfile.TemporaryDirectory(prefix="olf-scaffold-verify-") as tmp:
        verify_lakehouse = Path(tmp) / "lakehouse_code"
        (verify_lakehouse / "bronze").mkdir(parents=True)
        verify_schema_dir = Path(tmp) / "docs" / "schema"
        verify_schema_dir.mkdir(parents=True)
        for schema_name in ("lakehouse.schema.json", "source.schema.json"):
            shutil.copy(repo_root / "docs" / "schema" / schema_name, verify_schema_dir / schema_name)
        for source_yaml in sorted(lakehouse_src.glob("bronze/*/source.yaml")):
            destination = verify_lakehouse / source_yaml.relative_to(lakehouse_src)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(source_yaml, destination)
        for scaffold_file in plan.files:
            if not scaffold_file.relative_path.startswith(_BRONZE_PREFIX):
                continue
            if not scaffold_file.relative_path.endswith(_SOURCE_YAML_SUFFIX):
                continue
            destination = Path(tmp) / scaffold_file.relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(scaffold_file.content, encoding="utf-8")
        (verify_lakehouse / "lakehouse.yaml").write_text(plan.lakehouse_yaml, encoding="utf-8")

        errors = descriptor_schema_errors(Path(tmp))
        if errors:
            raise ScaffoldError("; ".join(errors))
        try:
            inventory = load_lakehouse_inventory(Path(tmp))
        except LakehouseDescriptorError as exc:
            raise ScaffoldError(str(exc)) from exc
        _check_asset_key_collisions(inventory)


def _write(repo_root: Path, plan: ScaffoldPlan) -> None:
    """Write every file in `plan`. `_verify` already proved the plan valid,
    so failure here means a filesystem-level problem (permission denied,
    disk full, a target that changed underneath us) that pre-checking
    `.exists()` can't catch in advance. On any such failure, undo every
    write already made this call -- delete each brand-new file
    (`plan.files`), restore the pre-existing content of each edited file
    (`plan.edits`) -- so a partial write never strands the repository in a
    state a retry can't cleanly build on top of.
    """
    lakehouse_target = repo_root / "lakehouse_code" / "lakehouse.yaml"
    created: list[Path] = []
    # lakehouse.yaml is itself an edit to a pre-existing file -- its
    # original content must be saved and restorable exactly like the other
    # edits (e.g. a Floe contract), not treated as a special last step that
    # a failure partway through its own write would leave corrupted.
    original_edit_content: dict[Path, str] = {lakehouse_target: lakehouse_target.read_text(encoding="utf-8")}
    try:
        for scaffold_file in plan.edits:
            target = repo_root / scaffold_file.relative_path
            original_edit_content[target] = target.read_text(encoding="utf-8")
        for scaffold_file in plan.files:
            target = repo_root / scaffold_file.relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(scaffold_file.content, encoding="utf-8")
            created.append(target)
        for scaffold_file in plan.edits:
            target = repo_root / scaffold_file.relative_path
            target.write_text(scaffold_file.content, encoding="utf-8")
        lakehouse_target.write_text(plan.lakehouse_yaml, encoding="utf-8")
    except OSError:
        for target in created:
            target.unlink(missing_ok=True)
        for target, content in original_edit_content.items():
            target.write_text(content, encoding="utf-8")
        raise


def commit_plan(repo_root: Path, plan: ScaffoldPlan) -> None:
    """Verify `plan` against the canonical model in an isolated temp tree,
    then write every file for real. Writes nothing on failure."""
    check_no_existing_targets(repo_root, plan.files)
    _verify(repo_root, plan)
    _write(repo_root, plan)
