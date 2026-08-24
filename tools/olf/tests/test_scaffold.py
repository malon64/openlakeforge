"""Tests for `olf source|domain|product new` (issue #40 golden-path scaffolding).

Every test seeds a throwaway copy of the real `lakehouse_code` tree plus the
JSON Schemas into `tmp_path`, then drives the scaffold engine or its Typer
commands against that copy -- never against the checked-in repository.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from openlakeforge_domain import load_lakehouse_inventory
from typer.testing import CliRunner

from olf.cli import app
from olf.scaffold._commit import commit_plan
from olf.scaffold._shared import ScaffoldError
from olf.scaffold.domain import plan_domain_new
from olf.scaffold.product import plan_product_new
from olf.scaffold.source import plan_source_new

ROOT = Path(__file__).resolve().parents[3]
runner = CliRunner()


def _seed_repo(tmp_path: Path) -> Path:
    """A throwaway repo root carrying the real lakehouse_code tree and schemas."""
    shutil.copytree(ROOT / "lakehouse_code", tmp_path / "lakehouse_code")
    schema_dir = tmp_path / "docs" / "schema"
    schema_dir.mkdir(parents=True)
    shutil.copy(ROOT / "docs/schema/lakehouse.schema.json", schema_dir / "lakehouse.schema.json")
    shutil.copy(ROOT / "docs/schema/source.schema.json", schema_dir / "source.schema.json")
    return tmp_path


def _tree(repo_root: Path) -> set[str]:
    return {str(p.relative_to(repo_root)) for p in repo_root.rglob("*") if p.is_file()}


def _run(*args: str) -> object:
    result = runner.invoke(app, list(args))
    assert result.exit_code == 0, result.output
    return result


# --------------------------------------------------------------------------
# olf source new
# --------------------------------------------------------------------------


def test_source_new_generates_the_documented_file_tree(tmp_path: Path) -> None:
    repo_root = _seed_repo(tmp_path)

    plan = plan_source_new(repo_root, source="marketing_platform", display_name=None, resources=("campaigns",))
    commit_plan(repo_root, plan)

    bronze = repo_root / "lakehouse_code" / "bronze" / "marketing_platform"
    assert (bronze / "source.yaml").is_file()
    assert (bronze / "dlt" / "__init__.py").is_file()
    assert (bronze / "dlt" / "marketing_platform.py").is_file()
    assert (bronze / "examples" / "campaigns.csv").is_file()
    assert (bronze / "README.md").is_file()

    inventory = load_lakehouse_inventory(repo_root)
    assert "marketing_platform" in inventory.source_names


def test_source_new_rejects_bad_identifier_and_writes_nothing(tmp_path: Path) -> None:
    repo_root = _seed_repo(tmp_path)
    before = _tree(repo_root)
    lakehouse_before = (repo_root / "lakehouse_code" / "lakehouse.yaml").read_text()

    with pytest.raises(ScaffoldError, match=r"must match"):
        plan_source_new(repo_root, source="Bad-Name", display_name=None, resources=("x",))

    assert _tree(repo_root) == before
    assert (repo_root / "lakehouse_code" / "lakehouse.yaml").read_text() == lakehouse_before


def test_source_new_rejects_duplicate_source_and_writes_nothing(tmp_path: Path) -> None:
    repo_root = _seed_repo(tmp_path)
    before = _tree(repo_root)

    with pytest.raises(ScaffoldError, match=r"already exists"):
        plan_source_new(repo_root, source="crm", display_name=None, resources=("x",))

    assert _tree(repo_root) == before


def test_source_new_refuses_to_overwrite_an_existing_target_file(tmp_path: Path) -> None:
    repo_root = _seed_repo(tmp_path)
    plan = plan_source_new(repo_root, source="marketing_platform", display_name=None, resources=("campaigns",))
    commit_plan(repo_root, plan)
    before = _tree(repo_root)
    lakehouse_before = (repo_root / "lakehouse_code" / "lakehouse.yaml").read_text()

    with pytest.raises(ScaffoldError, match=r"already exists in lakehouse.yaml"):
        plan = plan_source_new(repo_root, source="marketing_platform", display_name=None, resources=("campaigns",))
        commit_plan(repo_root, plan)

    assert _tree(repo_root) == before
    assert (repo_root / "lakehouse_code" / "lakehouse.yaml").read_text() == lakehouse_before


# --------------------------------------------------------------------------
# olf domain new
# --------------------------------------------------------------------------


def test_domain_new_creates_a_product_less_domain_that_validates_on_its_own(tmp_path: Path) -> None:
    repo_root = _seed_repo(tmp_path)
    source_plan = plan_source_new(repo_root, source="workday", display_name=None, resources=("employees",))
    commit_plan(repo_root, source_plan)

    plan = plan_domain_new(repo_root, domain="hr", display_name="HR", inputs=(("workday", "employees"),))
    commit_plan(repo_root, plan)

    inventory = load_lakehouse_inventory(repo_root)
    hr = next(d for d in inventory.domains if d.name == "hr")
    assert hr.products == ()
    assert hr.silver_namespace == "hr_silver"
    assert {t.name for t in hr.silver_tables} == {"employees"}


def test_domain_new_rejects_unresolved_source_resource_and_writes_nothing(tmp_path: Path) -> None:
    repo_root = _seed_repo(tmp_path)
    before = _tree(repo_root)

    with pytest.raises(ScaffoldError, match=r"unknown source 'nonexistent'"):
        plan_domain_new(repo_root, domain="hr", display_name=None, inputs=(("nonexistent", "employees"),))

    assert _tree(repo_root) == before

    with pytest.raises(ScaffoldError, match=r"source 'crm' has no resource 'nonexistent_resource'"):
        plan_domain_new(repo_root, domain="hr", display_name=None, inputs=(("crm", "nonexistent_resource"),))

    assert _tree(repo_root) == before


def test_domain_new_rejects_duplicate_domain_and_writes_nothing(tmp_path: Path) -> None:
    repo_root = _seed_repo(tmp_path)
    before = _tree(repo_root)

    with pytest.raises(ScaffoldError, match=r"already exists"):
        plan_domain_new(repo_root, domain="sales", display_name=None, inputs=(("crm", "orders"),))

    assert _tree(repo_root) == before


def test_domain_new_requires_at_least_one_input(tmp_path: Path) -> None:
    repo_root = _seed_repo(tmp_path)

    with pytest.raises(ScaffoldError, match=r"at least one --input"):
        plan_domain_new(repo_root, domain="hr", display_name=None, inputs=())


# --------------------------------------------------------------------------
# olf product new
# --------------------------------------------------------------------------


def test_product_new_creates_first_product_and_domain_implicitly(tmp_path: Path) -> None:
    repo_root = _seed_repo(tmp_path)
    source_plan = plan_source_new(repo_root, source="marketing_platform", display_name=None, resources=("campaigns",))
    commit_plan(repo_root, source_plan)

    plan = plan_product_new(
        repo_root,
        target="marketing/campaign_performance",
        display_name=None,
        silver_inputs=(),
        inputs=(("marketing_platform", "campaigns"),),
        gold_tables=("mart_campaign_performance",),
        with_report=False,
    )
    commit_plan(repo_root, plan)

    gold = repo_root / "lakehouse_code" / "gold" / "campaign_performance"
    assert (gold / "dbt" / "dbt_project.yml").is_file()
    assert (gold / "dbt" / "models" / "gold" / "mart_campaign_performance.sql").is_file()
    assert (gold / "dbt" / "models" / "gold" / "schema.yml").is_file()
    assert (gold / "dbt" / "models" / "sources.yml").is_file()
    assert (repo_root / "lakehouse_code" / "pipelines" / "dagster" / "campaign_performance.py").is_file()
    assert (repo_root / "lakehouse_code" / "silver" / "marketing" / "contracts" / "floe" / "marketing.yml").is_file()

    inventory = load_lakehouse_inventory(repo_root)
    product = next(p for p in inventory.products if p.id == "campaign_performance")
    assert product.domain_name == "marketing"
    assert product.gold_namespace == "campaign_performance_gold"


def test_product_new_adds_second_product_to_existing_domain_sharing_silver(tmp_path: Path) -> None:
    repo_root = _seed_repo(tmp_path)

    plan = plan_product_new(
        repo_root,
        target="sales/order_summary",
        display_name=None,
        silver_inputs=("orders",),
        inputs=(),
        gold_tables=("mart_order_summary",),
        with_report=False,
    )
    commit_plan(repo_root, plan)

    inventory = load_lakehouse_inventory(repo_root)
    sales = next(d for d in inventory.domains if d.name == "sales")
    assert {p.id for p in sales.products} == {"order_revenue", "customer_health", "order_summary"}
    order_summary = next(p for p in sales.products if p.id == "order_summary")
    assert order_summary.silver_namespace == "sales_silver"


def test_one_source_resource_shared_by_two_domains_creates_no_second_ingestion(tmp_path: Path) -> None:
    """Acceptance criterion: a single Source resource referenced by multiple
    downstream domains/products must not generate a duplicate dlt loader or
    duplicate Bronze ownership."""
    repo_root = _seed_repo(tmp_path)
    crm_files_before = _tree(repo_root / "lakehouse_code" / "bronze" / "crm")

    plan = plan_domain_new(repo_root, domain="finance", display_name="Finance", inputs=(("crm", "orders"),))
    commit_plan(repo_root, plan)

    crm_files_after = _tree(repo_root / "lakehouse_code" / "bronze" / "crm")
    assert crm_files_after == crm_files_before

    inventory = load_lakehouse_inventory(repo_root)
    sales = next(d for d in inventory.domains if d.name == "sales")
    finance = next(d for d in inventory.domains if d.name == "finance")
    sales_orders = next(t for t in sales.silver_tables if t.name == "orders")
    finance_orders = next(t for t in finance.silver_tables if t.name == "orders")
    assert (sales_orders.source, sales_orders.resource) == ("crm", "orders")
    assert (finance_orders.source, finance_orders.resource) == ("crm", "orders")


def test_product_new_extends_existing_domain_with_new_input(tmp_path: Path) -> None:
    repo_root = _seed_repo(tmp_path)
    source_plan = plan_source_new(repo_root, source="workday", display_name=None, resources=("employees", "absences"))
    commit_plan(repo_root, source_plan)
    domain_plan = plan_domain_new(repo_root, domain="hr", display_name="HR", inputs=(("workday", "employees"),))
    commit_plan(repo_root, domain_plan)

    plan = plan_product_new(
        repo_root,
        target="hr/headcount",
        display_name=None,
        silver_inputs=("employees",),
        inputs=(("workday", "absences"),),
        gold_tables=("mart_headcount", "mart_absence_rate"),
        with_report=False,
    )
    commit_plan(repo_root, plan)

    contract_text = (repo_root / "lakehouse_code" / "silver" / "hr" / "contracts" / "floe" / "hr.yml").read_text()
    assert contract_text.count("incremental_mode") == 2  # employees + absences entities

    inventory = load_lakehouse_inventory(repo_root)
    hr = next(d for d in inventory.domains if d.name == "hr")
    assert {t.name for t in hr.silver_tables} == {"employees", "absences"}
    headcount = next(p for p in hr.products if p.id == "headcount")
    assert set(headcount.silver_inputs) == {"employees", "absences"}


def test_product_new_rejects_when_domain_missing_and_no_input_given(tmp_path: Path) -> None:
    repo_root = _seed_repo(tmp_path)

    with pytest.raises(ScaffoldError, match=r"does not exist yet"):
        plan_product_new(
            repo_root,
            target="hr/headcount",
            display_name=None,
            silver_inputs=(),
            inputs=(),
            gold_tables=("mart_x",),
            with_report=False,
        )


def test_product_new_rejects_silver_input_when_domain_does_not_exist(tmp_path: Path) -> None:
    repo_root = _seed_repo(tmp_path)

    with pytest.raises(ScaffoldError, match=r"does not exist yet"):
        plan_product_new(
            repo_root,
            target="hr/headcount",
            display_name=None,
            silver_inputs=("employees",),
            inputs=(("crm", "orders"),),
            gold_tables=("mart_x",),
            with_report=False,
        )


def test_product_new_rejects_unresolved_silver_input(tmp_path: Path) -> None:
    repo_root = _seed_repo(tmp_path)

    with pytest.raises(ScaffoldError, match=r"has no Silver table 'nonexistent'"):
        plan_product_new(
            repo_root,
            target="sales/new_product",
            display_name=None,
            silver_inputs=("nonexistent",),
            inputs=(),
            gold_tables=("mart_x",),
            with_report=False,
        )


def test_product_new_rejects_duplicate_global_product_id(tmp_path: Path) -> None:
    repo_root = _seed_repo(tmp_path)
    before = _tree(repo_root)

    with pytest.raises(ScaffoldError, match=r"globally unique"):
        plan_product_new(
            repo_root,
            target="sales/order_revenue",
            display_name=None,
            silver_inputs=("orders",),
            inputs=(),
            gold_tables=("mart_x",),
            with_report=False,
        )

    assert _tree(repo_root) == before


def test_product_new_requires_at_least_one_gold_table(tmp_path: Path) -> None:
    repo_root = _seed_repo(tmp_path)

    with pytest.raises(ScaffoldError, match=r"at least one --gold-table"):
        plan_product_new(
            repo_root,
            target="sales/new_product",
            display_name=None,
            silver_inputs=("orders",),
            inputs=(),
            gold_tables=(),
            with_report=False,
        )


def test_product_new_with_report_generates_superset_skeleton_and_registers_dashboard(tmp_path: Path) -> None:
    repo_root = _seed_repo(tmp_path)

    plan = plan_product_new(
        repo_root,
        target="sales/order_summary",
        display_name=None,
        silver_inputs=("orders",),
        inputs=(),
        gold_tables=("mart_order_summary",),
        with_report=True,
    )
    commit_plan(repo_root, plan)

    dashboard_dir = repo_root / "lakehouse_code" / "dashboards" / "superset" / "order_summary"
    assert (dashboard_dir / "metadata.yaml").is_file()
    assert (dashboard_dir / "databases" / "openlakeforge_trino.yaml").is_file()
    assert (dashboard_dir / "datasets" / "OpenLakeForge_Trino" / "mart_order_summary.yaml").is_file()

    inventory = load_lakehouse_inventory(repo_root)
    assert any(d.name == "order_summary" and d.products == ("order_summary",) for d in inventory.dashboards)


def test_running_the_same_command_twice_is_refused_not_reapplied(tmp_path: Path) -> None:
    repo_root = _seed_repo(tmp_path)
    plan = plan_source_new(repo_root, source="workday", display_name=None, resources=("employees",))
    commit_plan(repo_root, plan)
    lakehouse_after_first = (repo_root / "lakehouse_code" / "lakehouse.yaml").read_text()

    with pytest.raises(ScaffoldError):
        plan_again = plan_source_new(repo_root, source="workday", display_name=None, resources=("employees",))
        commit_plan(repo_root, plan_again)

    assert (repo_root / "lakehouse_code" / "lakehouse.yaml").read_text() == lakehouse_after_first


# --------------------------------------------------------------------------
# Cross-artifact consistency
# --------------------------------------------------------------------------


def test_generated_product_artifacts_are_internally_consistent(tmp_path: Path) -> None:
    repo_root = _seed_repo(tmp_path)
    source_plan = plan_source_new(repo_root, source="marketing_platform", display_name=None, resources=("campaigns",))
    commit_plan(repo_root, source_plan)
    plan = plan_product_new(
        repo_root,
        target="marketing/campaign_performance",
        display_name=None,
        silver_inputs=(),
        inputs=(("marketing_platform", "campaigns"),),
        gold_tables=("mart_campaign_performance",),
        with_report=False,
    )
    commit_plan(repo_root, plan)

    gold = repo_root / "lakehouse_code" / "gold" / "campaign_performance"
    sources_yml = (gold / "dbt" / "models" / "sources.yml").read_text()
    assert "schema: marketing_silver" in sources_yml

    floe_contract = (
        repo_root / "lakehouse_code" / "silver" / "marketing" / "contracts" / "floe" / "marketing.yml"
    ).read_text()
    assert 'namespace: "marketing_silver"' in floe_contract

    dagster_module = (repo_root / "lakehouse_code" / "pipelines" / "dagster" / "campaign_performance.py").read_text()
    assert '"campaigns"' in dagster_module
    assert '("marketing_platform", "campaigns")' in dagster_module


# --------------------------------------------------------------------------
# CLI wiring smoke test
# --------------------------------------------------------------------------


def test_cli_source_domain_product_new_are_registered() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "source" in result.output
    assert "domain" in result.output
    assert "product" in result.output


def test_cli_source_new_end_to_end(tmp_path: Path) -> None:
    repo_root = _seed_repo(tmp_path)
    _run(
        "source", "new", "marketing_platform",
        "--resource", "campaigns",
        "--repo-root", str(repo_root),
    )
    inventory = load_lakehouse_inventory(repo_root)
    assert "marketing_platform" in inventory.source_names
