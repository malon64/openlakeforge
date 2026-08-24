"""Tests for `olf source|domain|product new` (issue #40 golden-path scaffolding).

Every test seeds a throwaway copy of the real `lakehouse_code` tree plus the
JSON Schemas into `tmp_path`, then drives the scaffold engine or its Typer
commands against that copy -- never against the checked-in repository.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest
import yaml
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


def test_readme_links_from_generated_bronze_and_silver_dirs_resolve(tmp_path: Path) -> None:
    """The Bronze and Silver READMEs link back to docs/ with a relative
    path; that path must actually resolve from where the README lives."""
    repo_root = _seed_repo(tmp_path)
    source_plan = plan_source_new(repo_root, source="marketing_platform", display_name=None, resources=("campaigns",))
    commit_plan(repo_root, source_plan)
    domain_plan = plan_domain_new(
        repo_root, domain="marketing", display_name=None, inputs=(("marketing_platform", "campaigns"),)
    )
    commit_plan(repo_root, domain_plan)

    bronze_readme = repo_root / "lakehouse_code" / "bronze" / "marketing_platform" / "README.md"
    domain_readme = repo_root / "lakehouse_code" / "silver" / "marketing" / "README.md"
    bronze_link = re.search(r"\]\((\.\./[^)]+)\)", bronze_readme.read_text(encoding="utf-8")).group(1)
    domain_link = re.search(r"\]\((\.\./[^)]+)\)", domain_readme.read_text(encoding="utf-8")).group(1)

    assert (bronze_readme.parent / bronze_link).resolve().is_relative_to(repo_root.resolve())
    assert (bronze_readme.parent / bronze_link).resolve() == (repo_root / "docs/getting-started/first-data-product.md")
    assert (domain_readme.parent / domain_link).resolve().is_relative_to(repo_root.resolve())
    assert (domain_readme.parent / domain_link).resolve() == (
        repo_root / "docs/adr/0026-medallion-ownership-and-catalog-namespace-contract.md"
    )


def test_source_new_handles_a_flow_style_sources_list(tmp_path: Path) -> None:
    """`sources: [crm, erp]` is valid, schema-accepted YAML the descriptor
    could legitimately use; appending to it must not splice a block-style
    list item directly after the flow list (invalid YAML)."""
    repo_root = _seed_repo(tmp_path)
    lakehouse_path = repo_root / "lakehouse_code" / "lakehouse.yaml"
    text = lakehouse_path.read_text(encoding="utf-8")
    text = re.sub(r"sources:\n(  - \w+\n)+", "sources: [crm, erp]\n", text)
    lakehouse_path.write_text(text, encoding="utf-8")
    assert yaml.safe_load(text)["sources"] == ["crm", "erp"]

    plan = plan_source_new(repo_root, source="marketing_platform", display_name=None, resources=("campaigns",))
    commit_plan(repo_root, plan)

    inventory = load_lakehouse_inventory(repo_root)
    assert set(inventory.source_names) == {"crm", "erp", "marketing_platform"}


def test_product_new_handles_a_flow_style_dashboards_list_with_mappings(tmp_path: Path) -> None:
    """`dashboards: [{name: x, products: [a]}]` is valid, schema-accepted
    YAML; converting it must re-parse each mapping item rather than split
    the flow text on commas, which would break on the commas inside the
    nested `products: [...]` value."""
    repo_root = _seed_repo(tmp_path)
    lakehouse_path = repo_root / "lakehouse_code" / "lakehouse.yaml"
    text = lakehouse_path.read_text(encoding="utf-8")
    text = re.sub(
        r"dashboards:\n(.*\n)+?$",
        "dashboards: [{name: sales_order_revenue, products: [order_revenue, customer_health]}]\n",
        text,
    )
    lakehouse_path.write_text(text, encoding="utf-8")
    assert yaml.safe_load(text)["dashboards"] == [
        {"name": "sales_order_revenue", "products": ["order_revenue", "customer_health"]}
    ]

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

    inventory = load_lakehouse_inventory(repo_root)
    dashboard_names = {d.name: d.products for d in inventory.dashboards}
    assert dashboard_names["sales_order_revenue"] == ("order_revenue", "customer_health")
    assert dashboard_names["order_summary"] == ("order_summary",)


def test_product_new_handles_a_flow_style_non_empty_products_list(tmp_path: Path) -> None:
    """A domain may represent its existing products as a schema-valid,
    non-empty flow sequence (`products: [{id: ..., ...}]`); appending a new
    product must convert it, not fail to even locate the field."""
    repo_root = _seed_repo(tmp_path)
    lakehouse_path = repo_root / "lakehouse_code" / "lakehouse.yaml"
    text = lakehouse_path.read_text(encoding="utf-8")
    text = re.sub(
        r"    products:\n(      - id: order_revenue\n(?:.*\n)*?)(?=  - name: supply_chain)",
        "    products: [{id: order_revenue, displayName: Order Revenue, description: d, "
        "status: active, silver_inputs: [orders], gold_tables: {tables: [{name: mart_order_revenue}]}}, "
        "{id: customer_health, displayName: Customer Health, description: d, status: active, "
        "silver_inputs: [accounts], gold_tables: {tables: [{name: mart_customer_health}]}}]\n",
        text,
    )
    lakehouse_path.write_text(text, encoding="utf-8")
    parsed = yaml.safe_load(text)
    sales_domain = next(d for d in parsed["domains"] if d["name"] == "sales")
    assert [p["id"] for p in sales_domain["products"]] == ["order_revenue", "customer_health"]

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


def test_source_new_preserves_a_trailing_comment_after_a_flow_style_list(tmp_path: Path) -> None:
    """Converting a flow-style `sources: [crm, erp]` to block style must not
    delete unrelated content (a comment, a blank line) that sits between the
    flow-list line and the next top-level key."""
    repo_root = _seed_repo(tmp_path)
    lakehouse_path = repo_root / "lakehouse_code" / "lakehouse.yaml"
    text = lakehouse_path.read_text(encoding="utf-8")
    text = re.sub(
        r"sources:\n(  - \w+\n)+",
        "sources: [crm, erp]\n# TODO: add the marketing source once it's ready.\n\n",
        text,
    )
    lakehouse_path.write_text(text, encoding="utf-8")

    plan = plan_source_new(repo_root, source="marketing_platform", display_name=None, resources=("campaigns",))
    commit_plan(repo_root, plan)

    result_text = lakehouse_path.read_text(encoding="utf-8")
    assert "# TODO: add the marketing source once it's ready." in result_text
    inventory = load_lakehouse_inventory(repo_root)
    assert set(inventory.source_names) == {"crm", "erp", "marketing_platform"}


def test_product_new_with_report_handles_a_lakehouse_missing_its_final_newline(tmp_path: Path) -> None:
    """`dashboards:` is the document's last top-level key, so appending to
    it is the one insertion point that can land at true end-of-file. If the
    file itself doesn't end in a newline, the new dashboard must not be
    concatenated directly onto the unterminated last line."""
    repo_root = _seed_repo(tmp_path)
    lakehouse_path = repo_root / "lakehouse_code" / "lakehouse.yaml"
    text = lakehouse_path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    lakehouse_path.write_text(text.rstrip("\n"), encoding="utf-8")

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

    inventory = load_lakehouse_inventory(repo_root)
    assert any(d.name == "order_summary" for d in inventory.dashboards)


def test_domain_new_handles_a_flow_style_domains_list(tmp_path: Path) -> None:
    """`domains: [{name: sales, ...}]` is valid, schema-accepted YAML;
    appending a new domain must convert it, not splice a block-style item
    after the already-closed flow value."""
    repo_root = _seed_repo(tmp_path)
    lakehouse_path = repo_root / "lakehouse_code" / "lakehouse.yaml"
    text = lakehouse_path.read_text(encoding="utf-8")
    domains_block_match = re.search(r"^domains:\n(.*\n)+?(?=^dashboards:)", text, re.MULTILINE)
    parsed_domains = yaml.safe_load(text)["domains"]
    flow_domains = "domains: " + yaml.safe_dump(parsed_domains, default_flow_style=True, width=10**9).strip() + "\n"
    text = text[: domains_block_match.start()] + flow_domains + text[domains_block_match.end() :]
    lakehouse_path.write_text(text, encoding="utf-8")
    assert yaml.safe_load(text)["domains"][0]["name"] == "sales"

    plan = plan_domain_new(repo_root, domain="hr", display_name="HR", inputs=(("crm", "orders"),))
    commit_plan(repo_root, plan)

    inventory = load_lakehouse_inventory(repo_root)
    assert {d.name for d in inventory.domains} == {"sales", "supply_chain", "hr"}


def test_product_new_reorders_products_before_silver_tables(tmp_path: Path) -> None:
    """Schema doesn't require `silver_tables` before `products` within a
    domain; appending a new product to a domain that reverses this order
    must land inside the products sequence, not past whatever field follows
    it."""
    repo_root = _seed_repo(tmp_path)
    lakehouse_path = repo_root / "lakehouse_code" / "lakehouse.yaml"
    text = lakehouse_path.read_text(encoding="utf-8")
    # Swap the sales domain's silver_tables: and products: blocks textually,
    # preserving the file's established block-style formatting exactly --
    # a full yaml.safe_dump round-trip would reformat with PyYAML's own
    # (differently-indented) list style, not what this module is built for.
    match = re.search(
        r"(    silver_tables:\n(?:      .*\n)+?)(    products:\n(?:      .*\n)+?)(?=  - name: supply_chain)",
        text,
    )
    assert match is not None
    text = text[: match.start()] + match.group(2) + match.group(1) + text[match.end() :]
    lakehouse_path.write_text(text, encoding="utf-8")
    reloaded_sales = next(d for d in yaml.safe_load(text)["domains"] if d["name"] == "sales")
    assert list(reloaded_sales.keys()).index("products") < list(reloaded_sales.keys()).index("silver_tables")

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
    assert "order_summary" in {p.id for p in sales.products}
    assert {t.name for t in sales.silver_tables} >= {"orders"}


def test_yaml_dq_escapes_newlines_and_control_characters(tmp_path: Path) -> None:
    """A literal newline in --display-name must not be silently folded into
    a space by YAML's plain-scalar rules; it has to be escaped so the exact
    string round-trips."""
    repo_root = _seed_repo(tmp_path)
    tricky_name = "HR\nOps"

    plan = plan_source_new(repo_root, source="workday", display_name=tricky_name, resources=("employees",))
    commit_plan(repo_root, plan)

    source_doc = yaml.safe_load((repo_root / "lakehouse_code" / "bronze" / "workday" / "source.yaml").read_text())
    assert source_doc["displayName"] == tricky_name


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


def test_domain_new_rejects_a_dagster_asset_key_collision_with_bronze(tmp_path: Path) -> None:
    """A domain named after an existing source, consuming a table named after
    one of that source's resources, produces a Silver asset key identical to
    the existing Bronze asset key -- Dagster's code location cannot load two
    assets sharing one key. Nothing should be written."""
    repo_root = _seed_repo(tmp_path)
    before = _tree(repo_root)

    with pytest.raises(ScaffoldError, match=r"asset key \('crm', 'orders'\) is claimed by both"):
        plan = plan_domain_new(repo_root, domain="crm", display_name=None, inputs=(("crm", "orders"),))
        commit_plan(repo_root, plan)

    assert _tree(repo_root) == before


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
    # This dashboard is not inventory.dashboards[0] (two dashboards already exist),
    # so `export-reports`' default-to-first-dashboard behavior would silently target
    # the wrong bundle unless the README tells the user to pin SUPERSET_REPORT_SOURCE_DIR.
    assert inventory.dashboards[0].name != "order_summary"
    readme = (dashboard_dir / "README.md").read_text(encoding="utf-8")
    assert "SUPERSET_REPORT_SOURCE_DIR=lakehouse_code/dashboards/superset/order_summary" in readme


def test_product_new_with_report_handles_an_inline_empty_dashboards_list(tmp_path: Path) -> None:
    """`dashboards:` has no schema minimum, so a lakehouse.yaml with no
    dashboards yet legitimately spells it `dashboards: []`. Appending the
    first dashboard must convert that to block style, not splice a list item
    directly after the inline empty list (which is invalid YAML)."""
    repo_root = _seed_repo(tmp_path)
    lakehouse_path = repo_root / "lakehouse_code" / "lakehouse.yaml"
    text = lakehouse_path.read_text(encoding="utf-8")
    text = text[: text.index("dashboards:")] + "dashboards: []\n"
    lakehouse_path.write_text(text, encoding="utf-8")
    assert yaml.safe_load(text)["dashboards"] == []

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

    inventory = load_lakehouse_inventory(repo_root)
    assert [d.name for d in inventory.dashboards] == ["order_summary"]


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
# YAML-safety and Superset-identity regressions
# --------------------------------------------------------------------------


def test_with_report_reuses_the_shared_superset_database_identity(tmp_path: Path) -> None:
    """Every checked-in Superset bundle registers the same 'OpenLakeForge
    Trino' database uuid; a generated bundle must attach to that same
    connection rather than registering a second, disconnected one."""
    repo_root = _seed_repo(tmp_path)
    shared_uuid = "8a87434c-559e-545d-badd-3575affe0185"
    existing_bundle = (
        ROOT / "lakehouse_code" / "dashboards" / "superset" / "sales_order_revenue" / "databases"
        / "openlakeforge_trino.yaml"
    )
    assert yaml.safe_load(existing_bundle.read_text())["uuid"] == shared_uuid

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
    database_doc = yaml.safe_load((dashboard_dir / "databases" / "openlakeforge_trino.yaml").read_text())
    dataset_doc = yaml.safe_load(
        (dashboard_dir / "datasets" / "OpenLakeForge_Trino" / "mart_order_summary.yaml").read_text()
    )
    assert database_doc["uuid"] == shared_uuid
    assert dataset_doc["database_uuid"] == shared_uuid


@pytest.mark.parametrize("tricky_name", ["HR & Ops: Team #1", 'Say "hi"', "Back\\slash"])
def test_display_name_with_yaml_metacharacters_round_trips(tmp_path: Path, tricky_name: str) -> None:
    """A --display-name containing YAML metacharacters (':', '#', quotes,
    backslashes) must not corrupt or break parsing of the generated
    descriptor -- across all three commands."""
    repo_root = _seed_repo(tmp_path)

    source_plan = plan_source_new(repo_root, source="workday", display_name=tricky_name, resources=("employees",))
    commit_plan(repo_root, source_plan)
    domain_plan = plan_domain_new(
        repo_root, domain="hr", display_name=tricky_name, inputs=(("workday", "employees"),)
    )
    commit_plan(repo_root, domain_plan)
    product_plan = plan_product_new(
        repo_root,
        target="hr/headcount",
        display_name=tricky_name,
        silver_inputs=("employees",),
        inputs=(),
        gold_tables=("mart_headcount",),
        with_report=False,
    )
    commit_plan(repo_root, product_plan)

    inventory = load_lakehouse_inventory(repo_root)
    assert "workday" in inventory.source_names
    hr = next(d for d in inventory.domains if d.name == "hr")
    assert hr.display_name == tricky_name
    headcount = next(p for p in hr.products if p.id == "headcount")
    assert headcount.display_name == tricky_name

    source_doc = yaml.safe_load((repo_root / "lakehouse_code" / "bronze" / "workday" / "source.yaml").read_text())
    assert source_doc["displayName"] == tricky_name


def test_csv_header_with_yaml_metacharacters_produces_valid_floe_contract(tmp_path: Path) -> None:
    """An example CSV header containing quotes/colons must not produce a
    Floe contract that fails to parse as YAML."""
    repo_root = _seed_repo(tmp_path)
    source_plan = plan_source_new(repo_root, source="workday", display_name=None, resources=("employees",))
    commit_plan(repo_root, source_plan)

    tricky_column = 'employee "nickname": primary'
    example_csv = repo_root / "lakehouse_code" / "bronze" / "workday" / "examples" / "employees.csv"
    example_csv.write_text(f"employees_id,{tricky_column}\nemployees-001,Al\n", encoding="utf-8")

    plan = plan_domain_new(repo_root, domain="hr", display_name=None, inputs=(("workday", "employees"),))
    commit_plan(repo_root, plan)

    contract_path = repo_root / "lakehouse_code" / "silver" / "hr" / "contracts" / "floe" / "hr.yml"
    contract_doc = yaml.safe_load(contract_path.read_text())
    columns = contract_doc["entities"][0]["schema"]["columns"]
    assert {column["name"] for column in columns} == {"employees_id", tricky_column}


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
