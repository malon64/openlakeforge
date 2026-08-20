import shutil
from pathlib import Path

import pytest

from olf import contracts_check

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).parent / "fixtures"


def _repo_with_schemas(tmp_path: Path) -> Path:
    """A synthetic repo root carrying the real, unmodified schema files."""
    schema_dir = tmp_path / "docs" / "schema"
    schema_dir.mkdir(parents=True)
    shutil.copy(ROOT / "docs/schema/lakehouse.schema.json", schema_dir / "lakehouse.schema.json")
    shutil.copy(ROOT / "docs/schema/source.schema.json", schema_dir / "source.schema.json")
    return tmp_path


def _write_descriptor(repo_root: Path, lakehouse_fixture: str, source_fixture: str = "valid_source.yaml") -> None:
    """Write the canonical lakehouse layout: lakehouse.yaml plus one source."""
    lakehouse_code = repo_root / "lakehouse_code"
    source_dir = lakehouse_code / "bronze" / "crm"
    source_dir.mkdir(parents=True, exist_ok=True)
    (lakehouse_code / "lakehouse.yaml").write_text(
        (FIXTURES / "descriptors" / lakehouse_fixture).read_text()
    )
    (source_dir / "source.yaml").write_text((FIXTURES / "descriptors" / source_fixture).read_text())


def test_descriptor_schema_conformance_passes_for_valid_lakehouse(tmp_path: Path) -> None:
    repo_root = _repo_with_schemas(tmp_path)
    _write_descriptor(repo_root, "valid_lakehouse.yaml")

    result = contracts_check._check_descriptor_schema_conformance(repo_root)

    assert result.ok, result.detail


def test_descriptor_schema_conformance_rejects_missing_bronze(tmp_path: Path) -> None:
    repo_root = _repo_with_schemas(tmp_path)
    _write_descriptor(repo_root, "invalid_lakehouse_missing_bronze.yaml")

    result = contracts_check._check_descriptor_schema_conformance(repo_root)

    assert not result.ok
    assert "bronze" in result.detail


def test_descriptor_schema_conformance_rejects_physical_fqn(tmp_path: Path) -> None:
    repo_root = _repo_with_schemas(tmp_path)
    _write_descriptor(repo_root, "invalid_lakehouse_physical_fqn.yaml")

    result = contracts_check._check_descriptor_schema_conformance(repo_root)

    assert not result.ok
    assert "fqn" in result.detail.lower() or "physical" in result.detail.lower()


def test_descriptor_schema_conformance_rejects_physical_path_in_lakehouse_table(tmp_path: Path) -> None:
    repo_root = _repo_with_schemas(tmp_path)
    _write_descriptor(repo_root, "invalid_lakehouse_physical_path.yaml")

    result = contracts_check._check_descriptor_schema_conformance(repo_root)

    assert not result.ok
    assert "path" in result.detail.lower() or "physical" in result.detail.lower()


def test_descriptor_schema_conformance_rejects_physical_path_in_source(tmp_path: Path) -> None:
    repo_root = _repo_with_schemas(tmp_path)
    _write_descriptor(repo_root, "valid_lakehouse.yaml", source_fixture="invalid_source_physical_path.yaml")

    result = contracts_check._check_descriptor_schema_conformance(repo_root)

    assert not result.ok
    assert "path" in result.detail.lower() or "physical" in result.detail.lower()


def test_descriptor_schema_conformance_fails_when_no_lakehouse_found(tmp_path: Path) -> None:
    repo_root = _repo_with_schemas(tmp_path)
    (repo_root / "lakehouse_code").mkdir()

    result = contracts_check._check_descriptor_schema_conformance(repo_root)

    assert not result.ok


def test_descriptor_schema_conformance_passes_against_real_repo() -> None:
    result = contracts_check._check_descriptor_schema_conformance(ROOT)

    assert result.ok, result.detail


def _repo_with_local_contracts(
    tmp_path: Path, contracts_tf_fixture: str, main_tf_fixture: str = "valid_main.tf"
) -> Path:
    """A synthetic repo root where `local`'s contracts.tf/main.tf come from a
    fixture under test, while azure-poc/aws-poc and the aws-glue module are
    copied unmodified from the real repo -- isolating the fixture as the one
    variable under test."""
    hcl_fixtures = FIXTURES / "hcl"
    for env in ("azure-poc", "aws-poc"):
        env_dir = tmp_path / "infra/terraform/environments" / env
        env_dir.mkdir(parents=True)
        shutil.copy(ROOT / "infra/terraform/environments" / env / "contracts.tf", env_dir / "contracts.tf")
        shutil.copy(ROOT / "infra/terraform/environments" / env / "main.tf", env_dir / "main.tf")

    local_dir = tmp_path / "infra/terraform/environments/local"
    local_dir.mkdir(parents=True)
    (local_dir / "contracts.tf").write_text((hcl_fixtures / contracts_tf_fixture).read_text())
    (local_dir / "main.tf").write_text((hcl_fixtures / main_tf_fixture).read_text())

    glue_dir = tmp_path / "infra/terraform/modules/catalog/aws-glue"
    glue_dir.mkdir(parents=True)
    shutil.copy(
        ROOT / "infra/terraform/modules/catalog/aws-glue/main.tf",
        glue_dir / "main.tf",
    )
    return tmp_path


def test_hcl_structured_contracts_passes_for_valid_fixture(tmp_path: Path) -> None:
    repo_root = _repo_with_local_contracts(tmp_path, "valid_local_contracts.tf")

    result = contracts_check._check_hcl_structured_contracts(repo_root)

    assert result.ok, result.detail


def test_hcl_structured_contracts_rejects_missing_required_local(tmp_path: Path) -> None:
    repo_root = _repo_with_local_contracts(tmp_path, "invalid_missing_required_local.tf")

    result = contracts_check._check_hcl_structured_contracts(repo_root)

    assert not result.ok
    assert "storage_contract" in result.detail


def test_hcl_structured_contracts_rejects_missing_check_block(tmp_path: Path) -> None:
    repo_root = _repo_with_local_contracts(tmp_path, "invalid_missing_check_block.tf")

    result = contracts_check._check_hcl_structured_contracts(repo_root)

    assert not result.ok
    assert "local_contract_adapters_are_explicit" in result.detail


def test_hcl_structured_contracts_rejects_forbidden_phase2_field(tmp_path: Path) -> None:
    repo_root = _repo_with_local_contracts(tmp_path, "invalid_forbidden_phase2_field.tf")

    result = contracts_check._check_hcl_structured_contracts(repo_root)

    assert not result.ok
    assert "catalog_namespaces" in result.detail


def test_hcl_structured_contracts_rejects_missing_glue_removed_block(tmp_path: Path) -> None:
    repo_root = _repo_with_local_contracts(tmp_path, "valid_local_contracts.tf")
    (repo_root / "infra/terraform/modules/catalog/aws-glue/main.tf").write_text(
        'locals {\n  rest_uri = "https://glue.example.amazonaws.com/iceberg"\n}\n'
    )

    result = contracts_check._check_hcl_structured_contracts(repo_root)

    assert not result.ok
    assert "removed" in result.detail.lower()


def test_hcl_structured_contracts_passes_against_real_repo() -> None:
    result = contracts_check._check_hcl_structured_contracts(ROOT)

    assert result.ok, result.detail


def test_hcl_phase_two_invariants_skips_when_no_applied_state(tmp_path: Path) -> None:
    result = contracts_check._check_hcl_phase_two_invariants(tmp_path)

    assert result.ok
    assert "skipped" in result.detail.lower()


def test_hcl_phase_two_invariants_passes_when_applied_state_is_clean(tmp_path: Path, monkeypatch) -> None:
    def fake_load(terraform_dir: str):
        return {"schema_version": "2.0.0", "catalog": {"catalog_database_fqn": "polaris.lakehouse_dev"}}

    monkeypatch.setattr(contracts_check.contracts_module, "load_provider_contracts", fake_load)

    result = contracts_check._check_hcl_phase_two_invariants(tmp_path)

    assert result.ok, result.detail
    assert "applied environment" in result.detail


def test_hcl_phase_two_invariants_rejects_forbidden_resolved_field(tmp_path: Path, monkeypatch) -> None:
    def fake_load(terraform_dir: str):
        return {"schema_version": "2.0.0", "catalog": {"catalog_namespaces": ["silver", "gold"]}}

    monkeypatch.setattr(contracts_check.contracts_module, "load_provider_contracts", fake_load)

    result = contracts_check._check_hcl_phase_two_invariants(tmp_path)

    assert not result.ok
    assert "catalog_namespaces" in result.detail


def test_floe_rendered_profile_passes_against_real_repo() -> None:
    result = contracts_check._check_floe_rendered_profile()

    assert result.ok, result.detail


def _write_floe_contract(repo_root: Path, domain: str, filename: str, fixture_name: str) -> None:
    contracts_dir = repo_root / "lakehouse_code" / "silver" / domain / "contracts" / "floe"
    contracts_dir.mkdir(parents=True, exist_ok=True)
    (contracts_dir / filename).write_text((FIXTURES / "floe" / fixture_name).read_text())


def test_floe_contract_structure_rejects_shared_silver_namespace(tmp_path: Path) -> None:
    _write_floe_contract(tmp_path, "sales", "fixture.yml", "invalid_contract_shared_silver_namespace.yml")

    result = contracts_check._check_floe_contract_structure(tmp_path)

    assert not result.ok
    assert "silver" in result.detail


def test_floe_contract_structure_passes_against_real_repo() -> None:
    result = contracts_check._check_floe_contract_structure(ROOT)

    assert result.ok, result.detail


def test_floe_profile_templates_passes_against_real_repo() -> None:
    result = contracts_check._check_floe_profile_templates(ROOT)

    assert result.ok, result.detail


def test_helm_values_as_data_rejects_missing_compute_log_manager(tmp_path: Path) -> None:
    values_dir = tmp_path / "infra/helm/values/local"
    values_dir.mkdir(parents=True)
    (values_dir / "dagster.yaml").write_text(
        (FIXTURES / "helm" / "invalid_dagster_values_missing_compute_log_manager.yaml").read_text()
    )

    result = contracts_check._check_helm_values_as_data(tmp_path)

    assert not result.ok
    assert "computeLogManager" in result.detail


def test_helm_values_as_data_passes_against_real_repo() -> None:
    result = contracts_check._check_helm_values_as_data(ROOT)

    assert result.ok, result.detail


def test_makefile_target_wiring_passes_against_real_repo() -> None:
    result = contracts_check._check_makefile_target_wiring(ROOT)

    assert result.ok, result.detail


def test_makefile_target_wiring_rejects_empty_kubeconfig(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(contracts_check, "_KUBE_WIRED_TARGETS", ("broken-forward",))
    (tmp_path / "Makefile").write_text(
        'broken-forward:\n\tKUBECONFIG="" KUBE_CONTEXT=some-context bash scripts/noop.sh\n'
    )

    result = contracts_check._check_makefile_target_wiring(tmp_path)

    assert not result.ok
    assert "KUBECONFIG" in result.detail


def test_run_contracts_check_rejects_missing_repo_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        contracts_check.run_contracts_check(tmp_path / "does-not-exist")


def test_run_contracts_check_passes_against_real_repo() -> None:
    report = contracts_check.run_contracts_check(ROOT)

    assert report.ok, report.render()
