"""Fourth-product conformance: scaffolding a product makes the platform discover it.

These tests are the executable form of the #40 acceptance criterion "the fourth
product requires no shared Terraform or platform-code changes": a product is
scaffolded into a throwaway tree and every derived platform expectation is then
asserted to include it, with no code edited in between.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from olf import scaffold
from olf.descriptors import discover_domains, discover_products

REPO_ROOT = Path(__file__).resolve().parents[3]

SPEC = {
    "domain": "logistics",
    "domain_display_name": "Logistics",
    "domain_description": "Throwaway conformance domain.",
    "owner": "logistics",
    "product": "route_efficiency",
    "product_display_name": "Logistics Route Efficiency",
    "product_description": "Route cost and duration marts.",
    "sources": [
        {
            "name": "routes",
            "description": "Raw CSV routes.",
            "primary_key": ["route_id"],
            "columns": [
                {"name": "route_id", "type": "string"},
                {"name": "region", "type": "string"},
                {"name": "distance_km", "type": "double"},
            ],
            "example_rows": [["RTE-1", "emea", "120.5"]],
        }
    ],
    "marts": [
        {
            "name": "mart_route_cost",
            "description": "Cost per route.",
            "dttm_col": "run_date",
            "sql": "select 1 as placeholder",
            "columns": [
                {"name": "run_date", "type": "date"},
                {"name": "region", "type": "string"},
                {"name": "cost_amount", "type": "double"},
            ],
            "metrics": [
                {"name": "sum__cost_amount", "expression": "SUM(cost_amount)"},
            ],
            "chart": {
                "name": "Route Cost by Region",
                "viz_type": "echarts_timeseries_bar",
                "x_axis": "run_date",
                "groupby": ["region"],
                "metrics": ["sum__cost_amount"],
            },
        }
    ],
}


@pytest.fixture
def scaffolded(tmp_path: Path) -> Path:
    spec = scaffold.parse_spec(SPEC)
    scaffold.scaffold_product(spec, tmp_path)
    return tmp_path


def test_scaffolded_product_is_discovered_with_derived_physical_names(scaffolded: Path) -> None:
    products = {product.asset_prefix: product for product in discover_products(scaffolded)}
    assert "logistics_route_efficiency" in products

    product = products["logistics_route_efficiency"]
    assert product.domain == "logistics"
    assert product.id == "route_efficiency"
    assert product.job_name == "logistics_route_efficiency_pipeline"
    assert product.silver_namespace == "logistics_route_efficiency_silver"
    assert product.gold_namespace == "logistics_route_efficiency_gold"
    assert product.manifest_key == (
        "floe/manifests/logistics/route_efficiency/route_efficiency.manifest.json"
    )
    assert product.dashboard_title == "Logistics Route Efficiency"
    assert product.gold_tables == ("mart_route_cost",)
    assert product.silver_tables == ("routes",)


def test_scaffolded_domain_becomes_a_dagster_code_location(scaffolded: Path) -> None:
    domains = discover_domains(scaffolded)
    assert [domain.name for domain in domains] == ["logistics"]
    assert (scaffolded / "domains/logistics/definitions.py").exists()


def test_scaffold_writes_the_full_product_owned_layout(scaffolded: Path) -> None:
    base = scaffolded / "domains/logistics"
    expected = [
        "domain.yaml",
        "__init__.py",
        "README.md",
        "definitions.py",
        "contracts/floe/route_efficiency.yml",
        "extract/dlt/route_efficiency.py",
        "pipelines/dagster/route_efficiency.py",
        "transformations/dbt/route_efficiency/dbt_project.yml",
        "transformations/dbt/route_efficiency/packages.yml",
        "transformations/dbt/route_efficiency/profiles.yml",
        "transformations/dbt/route_efficiency/models/sources.yml",
        "transformations/dbt/route_efficiency/models/gold/schema.yml",
        "transformations/dbt/route_efficiency/models/gold/mart_route_cost.sql",
        "examples/raw/route_efficiency/routes.csv",
        "reports/superset/route_efficiency/metadata.yaml",
        "reports/superset/route_efficiency/databases/openlakeforge_trino.yaml",
        "reports/superset/route_efficiency/datasets/OpenLakeForge_Trino/mart_route_cost.yaml",
        "reports/superset/route_efficiency/charts/Route_Cost_by_Region_1.yaml",
        "reports/superset/route_efficiency/dashboards/Logistics_Route_Efficiency_1.yaml",
    ]
    missing = [path for path in expected if not (base / path).exists()]
    assert missing == []


def test_scaffold_touches_nothing_outside_the_domain_tree(scaffolded: Path) -> None:
    written = {path.relative_to(scaffolded) for path in scaffolded.rglob("*") if path.is_file()}
    outside = sorted(str(path) for path in written if not str(path).startswith("domains/logistics/"))
    assert outside == []


def test_scaffolded_descriptor_carries_no_physical_fqns(scaffolded: Path) -> None:
    document = yaml.safe_load((scaffolded / "domains/logistics/domain.yaml").read_text())
    serialized = yaml.safe_dump(document)
    assert "fqn" not in serialized
    assert "fullyQualifiedName" not in serialized
    assert document["apiVersion"] == "openlakeforge.io/v1alpha1"
    assert document["kind"] == "Domain"


def test_scaffold_skips_existing_files_without_force(tmp_path: Path) -> None:
    spec = scaffold.parse_spec(SPEC)
    scaffold.scaffold_product(spec, tmp_path)
    loader = tmp_path / "domains/logistics/extract/dlt/route_efficiency.py"
    loader.write_text("# hand-edited\n", encoding="utf-8")

    result = scaffold.scaffold_product(spec, tmp_path)

    assert loader in result.skipped
    assert loader.read_text(encoding="utf-8") == "# hand-edited\n"


def test_scaffold_force_overwrites_existing_files(tmp_path: Path) -> None:
    spec = scaffold.parse_spec(SPEC)
    scaffold.scaffold_product(spec, tmp_path)
    loader = tmp_path / "domains/logistics/extract/dlt/route_efficiency.py"
    loader.write_text("# hand-edited\n", encoding="utf-8")

    scaffold.scaffold_product(spec, tmp_path, force=True)

    assert "load_all_entities_to_bronze" in loader.read_text(encoding="utf-8")


def test_adding_a_product_to_an_existing_domain_keeps_both(tmp_path: Path) -> None:
    scaffold.scaffold_product(scaffold.parse_spec(SPEC), tmp_path)
    second = {**SPEC, "product": "carrier_cost", "product_display_name": "Logistics Carrier Cost"}
    scaffold.scaffold_product(scaffold.parse_spec(second), tmp_path)

    products = {product.id for product in discover_products(tmp_path)}
    assert products == {"route_efficiency", "carrier_cost"}

    definitions = (tmp_path / "domains/logistics/definitions.py").read_text(encoding="utf-8")
    assert "carrier_cost.defs" in definitions
    assert "route_efficiency.defs" in definitions


def test_chart_referencing_an_undeclared_column_is_rejected() -> None:
    broken = {
        **SPEC,
        "marts": [
            {
                **SPEC["marts"][0],
                "chart": {**SPEC["marts"][0]["chart"], "groupby": ["nonexistent_column"]},
            }
        ],
    }
    with pytest.raises(scaffold.ScaffoldError, match="nonexistent_column"):
        scaffold.parse_spec(broken)


def test_chart_referencing_an_undeclared_metric_is_rejected() -> None:
    broken = {
        **SPEC,
        "marts": [
            {
                **SPEC["marts"][0],
                "chart": {**SPEC["marts"][0]["chart"], "metrics": ["sum__missing"]},
            }
        ],
    }
    with pytest.raises(scaffold.ScaffoldError, match="sum__missing"):
        scaffold.parse_spec(broken)


def test_unsupported_chart_viz_type_is_rejected() -> None:
    broken = {
        **SPEC,
        "marts": [
            {**SPEC["marts"][0], "chart": {**SPEC["marts"][0]["chart"], "viz_type": "big_number"}}
        ],
    }
    with pytest.raises(scaffold.ScaffoldError, match="big_number"):
        scaffold.parse_spec(broken)


def test_spec_without_sources_is_rejected() -> None:
    with pytest.raises(scaffold.ScaffoldError, match="at least one source is required"):
        scaffold.parse_spec({**SPEC, "sources": []})


def _pie_spec() -> dict:
    return {
        **SPEC,
        "marts": [
            {
                **SPEC["marts"][0],
                "chart": {
                    "name": "Cost Share",
                    "viz_type": "pie",
                    "groupby": ["region"],
                    "metrics": ["sum__cost_amount"],
                },
            }
        ],
    }


def test_pie_chart_emits_the_singular_metric_superset_reads(tmp_path: Path) -> None:
    """Pie reads a scalar `metric`; a `metrics` list imports but renders empty."""
    spec = scaffold.parse_spec(_pie_spec())
    scaffold.scaffold_product(spec, tmp_path)

    chart = yaml.safe_load(
        (
            tmp_path
            / "domains/logistics/reports/superset/route_efficiency/charts/Cost_Share_1.yaml"
        ).read_text()
    )

    assert chart["params"]["metric"] == "sum__cost_amount"
    assert "metrics" not in chart["params"]
    # Pie has no axis; these would be inert noise carried from the timeseries shape.
    assert "x_axis" not in chart["params"]
    assert "y_axis_format" not in chart["params"]
    assert chart["params"]["show_labels"] is True


def test_table_chart_emits_aggregate_query_mode_and_no_axis(tmp_path: Path) -> None:
    spec = scaffold.parse_spec(
        {
            **SPEC,
            "marts": [
                {
                    **SPEC["marts"][0],
                    "chart": {
                        "name": "Cost Table",
                        "viz_type": "table",
                        "groupby": ["region"],
                        "metrics": ["sum__cost_amount"],
                    },
                }
            ],
        }
    )
    scaffold.scaffold_product(spec, tmp_path)

    chart = yaml.safe_load(
        (
            tmp_path
            / "domains/logistics/reports/superset/route_efficiency/charts/Cost_Table_1.yaml"
        ).read_text()
    )

    assert chart["params"]["query_mode"] == "aggregate"
    assert chart["params"]["metrics"] == ["sum__cost_amount"]
    assert "x_axis" not in chart["params"]


def test_pie_chart_with_multiple_metrics_is_rejected() -> None:
    spec = _pie_spec()
    spec["marts"][0]["metrics"] = [
        {"name": "sum__cost_amount", "expression": "SUM(cost_amount)"},
        {"name": "avg__cost_amount", "expression": "AVG(cost_amount)", "metric_type": "avg"},
    ]
    spec["marts"][0]["chart"]["metrics"] = ["sum__cost_amount", "avg__cost_amount"]
    with pytest.raises(scaffold.ScaffoldError, match="exactly one metric"):
        scaffold.parse_spec(spec)


def test_yaml_sensitive_descriptions_survive_round_trip(tmp_path: Path) -> None:
    """Descriptions are unrestricted input; raw interpolation breaks or truncates YAML."""
    hostile = "Raw feed: daily exports # not a comment"
    spec_dict = {
        **SPEC,
        "product_description": hostile,
        "sources": [{**SPEC["sources"][0], "description": hostile}],
        "marts": [{**SPEC["marts"][0], "description": hostile}],
    }
    scaffold.scaffold_product(scaffold.parse_spec(spec_dict), tmp_path)

    base = tmp_path / "domains/logistics"
    sources = yaml.safe_load(
        (base / "transformations/dbt/route_efficiency/models/sources.yml").read_text()
    )
    assert sources["sources"][0]["tables"][0]["description"] == hostile

    gold = yaml.safe_load(
        (base / "transformations/dbt/route_efficiency/models/gold/schema.yml").read_text()
    )
    assert gold["models"][0]["description"] == hostile

    dataset = yaml.safe_load(
        (
            base
            / "reports/superset/route_efficiency/datasets/OpenLakeForge_Trino/mart_route_cost.yaml"
        ).read_text()
    )
    assert dataset["description"] == hostile

    dashboard = yaml.safe_load(
        (
            base
            / "reports/superset/route_efficiency/dashboards/Logistics_Route_Efficiency_1.yaml"
        ).read_text()
    )
    assert dashboard["description"] == hostile

    floe = yaml.safe_load((base / "contracts/floe/route_efficiency.yml").read_text())
    assert isinstance(floe["metadata"]["description"], str)


def test_marketing_product_in_the_repo_was_scaffolded_not_hand_wired() -> None:
    """The committed fourth product must be discoverable exactly like the seeds."""
    products = {product.asset_prefix: product for product in discover_products(REPO_ROOT)}
    assert "marketing_campaign_performance" in products
    product = products["marketing_campaign_performance"]
    assert product.job_name == "marketing_campaign_performance_pipeline"
    assert product.gold_namespace == "marketing_campaign_performance_gold"
    assert len(product.gold_tables) == 3
