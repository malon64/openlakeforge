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
from openlakeforge_domain import LakehouseDescriptorError, load_lakehouse_inventory

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
            load_lakehouse_inventory(Path(tmp))
        except LakehouseDescriptorError as exc:
            raise ScaffoldError(str(exc)) from exc


def _write(repo_root: Path, plan: ScaffoldPlan) -> None:
    for scaffold_file in (*plan.files, *plan.edits):
        target = repo_root / scaffold_file.relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(scaffold_file.content, encoding="utf-8")
    (repo_root / "lakehouse_code" / "lakehouse.yaml").write_text(plan.lakehouse_yaml, encoding="utf-8")


def commit_plan(repo_root: Path, plan: ScaffoldPlan) -> None:
    """Verify `plan` against the canonical model in an isolated temp tree,
    then write every file for real. Writes nothing on failure."""
    check_no_existing_targets(repo_root, plan.files)
    _verify(repo_root, plan)
    _write(repo_root, plan)
