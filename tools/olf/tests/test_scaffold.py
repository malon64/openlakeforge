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


def test_rerun_without_force_preserves_the_existing_descriptor_entry(tmp_path: Path) -> None:
    """Rerunning scaffold for an already-existing product with a *changed*
    spec and no --force must not let the descriptor drift from what is
    actually on disk. This product's contract, dbt models, datasets, and
    pipeline are all skipped (they already exist); previously the descriptor
    was rewritten from the new spec anyway, so discovery/Terraform would
    expect a mart_extra_metric gold table that the retained (unforced) dbt
    project and dataset files do not implement.
    """
    scaffold.scaffold_product(scaffold.parse_spec(SPEC), tmp_path)
    original_descriptor = yaml.safe_load(
        (tmp_path / "domains/logistics/domain.yaml").read_text(encoding="utf-8")
    )

    changed_spec_dict = {
        **SPEC,
        "marts": [
            *SPEC["marts"],
            {
                "name": "mart_extra_metric",
                "description": "A mart added in a later, unforced rerun.",
                "sql": "select 1 as placeholder",
                "columns": [{"name": "region", "type": "string"}],
            },
        ],
    }
    result = scaffold.scaffold_product(scaffold.parse_spec(changed_spec_dict), tmp_path)

    # The new mart's own file is written (it didn't exist before)...
    assert any(p.name == "mart_extra_metric.sql" for p in result.written)
    # ...but the descriptor entry for the product as a whole stays exactly
    # what it was: the source of truth still matches the retained files, not
    # the new spec that was only partially applied.
    descriptor = yaml.safe_load((tmp_path / "domains/logistics/domain.yaml").read_text(encoding="utf-8"))
    entry = next(p for p in descriptor["data_products"] if p["id"] == "route_efficiency")
    original_entry = next(p for p in original_descriptor["data_products"] if p["id"] == "route_efficiency")
    assert entry == original_entry
    assert "mart_extra_metric" not in [t["name"] for t in entry["gold_tables"]["tables"]]


def test_rerun_with_force_updates_the_descriptor_entry_to_match(tmp_path: Path) -> None:
    scaffold.scaffold_product(scaffold.parse_spec(SPEC), tmp_path)
    changed_spec_dict = {
        **SPEC,
        "marts": [
            *SPEC["marts"],
            {
                "name": "mart_extra_metric",
                "description": "A mart added in a forced rerun.",
                "sql": "select 1 as placeholder",
                "columns": [{"name": "region", "type": "string"}],
            },
        ],
    }
    scaffold.scaffold_product(scaffold.parse_spec(changed_spec_dict), tmp_path, force=True)

    descriptor = yaml.safe_load((tmp_path / "domains/logistics/domain.yaml").read_text(encoding="utf-8"))
    entry = next(p for p in descriptor["data_products"] if p["id"] == "route_efficiency")
    assert "mart_extra_metric" in [t["name"] for t in entry["gold_tables"]["tables"]]


def test_forced_rerun_removes_a_stale_mart_dropped_from_the_spec(tmp_path: Path) -> None:
    """Removing a mart in --force rerun must delete its generated .sql model
    (and Superset dataset/chart), not just stop writing it. Otherwise dbt
    keeps discovering the stale model while domain.yaml and the
    descriptor-derived e2e table count reflect only the new set.
    """
    two_mart_spec = {
        **SPEC,
        "marts": [
            SPEC["marts"][0],
            {
                "name": "mart_extra_metric",
                "description": "A mart present in the first scaffold only.",
                "sql": "select 1 as placeholder",
                "columns": [{"name": "region", "type": "string"}],
                "chart": {
                    "name": "Extra Metric Chart",
                    "viz_type": "table",
                    "groupby": ["region"],
                },
            },
        ],
    }
    scaffold.scaffold_product(scaffold.parse_spec(two_mart_spec), tmp_path)
    base = tmp_path / "domains/logistics"
    stale_model = base / "transformations/dbt/route_efficiency/models/gold/mart_extra_metric.sql"
    stale_dataset = base / "reports/superset/route_efficiency/datasets/OpenLakeForge_Trino/mart_extra_metric.yaml"
    stale_chart = base / "reports/superset/route_efficiency/charts/Extra_Metric_Chart_1.yaml"
    assert stale_model.exists()
    assert stale_dataset.exists()
    assert stale_chart.exists()

    result = scaffold.scaffold_product(scaffold.parse_spec(SPEC), tmp_path, force=True)

    assert not stale_model.exists()
    assert not stale_dataset.exists()
    assert not stale_chart.exists()
    assert stale_model in result.removed
    # The surviving mart's own file must not be touched by the cleanup.
    assert (base / "transformations/dbt/route_efficiency/models/gold/mart_route_cost.sql").exists()


def test_forced_rerun_without_removed_marts_deletes_nothing(tmp_path: Path) -> None:
    scaffold.scaffold_product(scaffold.parse_spec(SPEC), tmp_path)
    result = scaffold.scaffold_product(scaffold.parse_spec(SPEC), tmp_path, force=True)
    assert result.removed == []


def test_chart_name_with_colon_survives_in_dashboard_position_yaml(tmp_path: Path) -> None:
    """The standalone chart renderer already escapes the chart name; the
    dashboard's position_json sliceName is a second, independent
    interpolation of the same value that was not.
    """
    spec = scaffold.parse_spec(
        {
            **SPEC,
            "marts": [
                {
                    **SPEC["marts"][0],
                    "chart": {**SPEC["marts"][0]["chart"], "name": "Revenue: USD"},
                }
            ],
        }
    )
    scaffold.scaffold_product(spec, tmp_path)

    dashboard_path = (
        tmp_path
        / "domains/logistics/reports/superset/route_efficiency/dashboards/Logistics_Route_Efficiency_1.yaml"
    )
    dashboard = yaml.safe_load(dashboard_path.read_text())
    position = dashboard["position"]
    slice_names = [
        block["meta"]["sliceName"]
        for block in position.values()
        if isinstance(block, dict) and "sliceName" in block.get("meta", {})
    ]
    assert "Revenue: USD" in slice_names


def test_example_rows_with_commas_and_quotes_are_csv_quoted(tmp_path: Path) -> None:
    """A raw ",".join() breaks on a value containing a comma or quote (e.g. a
    customer name "Smith, Inc."): the row splits into the wrong number of
    fields and the strict Floe ingestion rejects or misreads it.
    """
    import csv
    import io

    spec_dict = {
        **SPEC,
        "sources": [
            {
                **SPEC["sources"][0],
                "columns": [
                    {"name": "route_id", "type": "string"},
                    {"name": "customer_name", "type": "string"},
                ],
                "example_rows": [["RTE-1", 'Smith, Inc. "Prime"']],
            }
        ],
    }
    scaffold.scaffold_product(scaffold.parse_spec(spec_dict), tmp_path)

    csv_text = (tmp_path / "domains/logistics/examples/raw/route_efficiency/routes.csv").read_text()
    rows = list(csv.reader(io.StringIO(csv_text)))
    assert rows[0] == ["route_id", "customer_name"]
    assert rows[1] == ["RTE-1", 'Smith, Inc. "Prime"']


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


def test_metric_free_mart_renders_valid_dataset_yaml(tmp_path: Path) -> None:
    """A dimension-only mart (no metrics) must still round-trip through YAML.

    `metrics:\\n{metrics or "  []"}columns:` glued the empty-list fallback
    directly onto the following key with no line break -- `metrics:\\n
    []columns:` -- which is not parseable YAML at all, not merely a semantic
    mismatch.
    """
    spec_dict = {
        **SPEC,
        "marts": [
            {
                "name": "mart_route_region",
                "description": "Region dimension only, no metrics.",
                "sql": "select 1 as placeholder",
                "columns": [
                    {"name": "region", "type": "string"},
                ],
            }
        ],
    }
    spec = scaffold.parse_spec(spec_dict)
    scaffold.scaffold_product(spec, tmp_path)

    dataset_path = (
        tmp_path
        / "domains/logistics/reports/superset/route_efficiency/datasets/OpenLakeForge_Trino/mart_route_region.yaml"
    )
    dataset = yaml.safe_load(dataset_path.read_text())

    assert dataset["metrics"] == []
    assert dataset["columns"] == [
        {"column_name": "region", "type": "VARCHAR", "groupby": True, "filterable": True}
    ]


@pytest.mark.parametrize("bad_domain", ["supply-chain", "Supply_Chain", "1supply", "supply chain", "../../etc"])
def test_non_identifier_domain_is_rejected(bad_domain: str) -> None:
    """domain/product are used as both domains/<...>/ path components and Python
    module segments (domains.<domain>.definitions). An unvalidated value like
    'supply-chain' would report scaffold success while writing an unimportable
    module, and a value containing '..' or '/' could write outside
    domains/<domain>/** entirely.
    """
    with pytest.raises(scaffold.ScaffoldError, match=r"must match '\^\[a-z\]\[a-z0-9_\]\*\$'"):
        scaffold.parse_spec({**SPEC, "domain": bad_domain})


@pytest.mark.parametrize("bad_product", ["route-efficiency", "Route_Efficiency", "2fast"])
def test_non_identifier_product_is_rejected(bad_product: str) -> None:
    with pytest.raises(scaffold.ScaffoldError, match=r"must match '\^\[a-z\]\[a-z0-9_\]\*\$'"):
        scaffold.parse_spec({**SPEC, "product": bad_product})


def test_valid_identifier_domain_and_product_are_accepted() -> None:
    spec = scaffold.parse_spec({**SPEC, "domain": "supply_chain2", "product": "route_efficiency_v2"})
    assert spec.domain == "supply_chain2"
    assert spec.product == "route_efficiency_v2"


def test_source_name_path_traversal_is_rejected() -> None:
    """A validly parsed spec could otherwise set a source name like
    '../../../../../escaped', later interpolated straight into
    `raw_dir / f"{src.name}.csv"` -- writing outside domains/<domain>/**
    while scaffold_product still reports success.
    """
    hostile = {
        **SPEC,
        "sources": [{**SPEC["sources"][0], "name": "../../../../../escaped"}],
    }
    with pytest.raises(scaffold.ScaffoldError, match=r"must match '\^\[a-z\]\[a-z0-9_\]\*\$'"):
        scaffold.parse_spec(hostile)


def test_mart_name_path_traversal_is_rejected() -> None:
    hostile = {
        **SPEC,
        "marts": [{**SPEC["marts"][0], "name": "../../../../../escaped"}],
    }
    with pytest.raises(scaffold.ScaffoldError, match=r"must match '\^\[a-z\]\[a-z0-9_\]\*\$'"):
        scaffold.parse_spec(hostile)


def test_chart_name_path_traversal_is_rejected() -> None:
    hostile = {
        **SPEC,
        "marts": [
            {
                **SPEC["marts"][0],
                "chart": {**SPEC["marts"][0]["chart"], "name": "../../../../../escaped"},
            }
        ],
    }
    with pytest.raises(scaffold.ScaffoldError, match="must not contain a path separator"):
        scaffold.parse_spec(hostile)


def test_product_display_name_path_traversal_is_rejected() -> None:
    """product_display_name is used to derive the dashboard filename
    (reports_dir / "dashboards" / f"{dashboard_file}_1.yaml") via a
    replace(" ", "_").replace("-", "_") that does not strip '/'.
    """
    with pytest.raises(scaffold.ScaffoldError, match="must not contain a path separator"):
        scaffold.parse_spec({**SPEC, "product_display_name": "../../../../../escaped"})


def test_dttm_col_is_validated_even_without_a_chart() -> None:
    """A typo'd or undeclared dttm_col previously bypassed validation entirely
    for a chartless mart, since the check sat after `if not mart.chart:
    continue`. It is still emitted as the Superset dataset's main_dttm_col
    regardless of whether a chart exists.
    """
    chartless_with_bad_dttm = {
        **SPEC,
        "marts": [
            {
                "name": "mart_dimension_only",
                "description": "No chart, bad dttm_col.",
                "sql": "select 1 as placeholder",
                "columns": [{"name": "region", "type": "string"}],
                "dttm_col": "not_a_declared_column",
            }
        ],
    }
    with pytest.raises(scaffold.ScaffoldError, match="dttm_col 'not_a_declared_column' is not a declared column"):
        scaffold.parse_spec(chartless_with_bad_dttm)


def test_dbt_source_description_survives_a_colon_in_the_display_name(tmp_path: Path) -> None:
    """Unlike the per-source descriptions, this description was built by raw
    f-string interpolation of product_display_name, which is unrestricted:
    'Route: Efficiency' produced `description: Floe-owned Route: Efficiency
    Silver Iceberg tables.`, invalid YAML that fails dbt parse.
    """
    spec = scaffold.parse_spec({**SPEC, "product_display_name": "Route: Efficiency"})
    scaffold.scaffold_product(spec, tmp_path)

    sources = yaml.safe_load(
        (tmp_path / "domains/logistics/transformations/dbt/route_efficiency/models/sources.yml").read_text()
    )
    assert sources["sources"][0]["description"] == "Floe-owned Route: Efficiency Silver Iceberg tables."


def test_metric_fields_survive_yaml_sensitive_content(tmp_path: Path) -> None:
    """verbose_name and expression are unrestricted by parse_spec (unlike
    metric names elsewhere), and were raw-interpolated into the Superset
    dataset YAML: 'Revenue: USD' produced invalid YAML, and an expression
    containing ' # ' would be silently truncated as a comment.
    """
    spec_dict = {
        **SPEC,
        "marts": [
            {
                **SPEC["marts"][0],
                "metrics": [
                    {
                        "name": "rev: total",
                        "expression": "SUM(cost_amount) # discounted",
                        "verbose_name": "Revenue: USD",
                    }
                ],
                "chart": {**SPEC["marts"][0]["chart"], "metrics": ["rev: total"]},
            }
        ],
    }
    scaffold.scaffold_product(scaffold.parse_spec(spec_dict), tmp_path)

    dataset = yaml.safe_load(
        (
            tmp_path
            / "domains/logistics/reports/superset/route_efficiency/datasets/OpenLakeForge_Trino/mart_route_cost.yaml"
        ).read_text()
    )
    metric = dataset["metrics"][0]
    assert metric["metric_name"] == "rev: total"
    assert metric["verbose_name"] == "Revenue: USD"
    assert metric["expression"] == "SUM(cost_amount) # discounted"


def test_chart_filename_collision_is_rejected() -> None:
    """'Spend-by Channel' and 'Spend by-Channel' both normalize to
    'Spend_by_Channel' once spaces and dashes are replaced with underscores.
    On first scaffold the second chart would be silently skipped; under
    --force it would overwrite the first -- either way the dashboard
    references two distinct chart UUIDs but the bundle contains only one
    chart asset.
    """
    spec_dict = {
        **SPEC,
        "marts": [
            {**SPEC["marts"][0], "name": "mart_a", "chart": {**SPEC["marts"][0]["chart"], "name": "Spend-by Channel"}},
            {**SPEC["marts"][0], "name": "mart_b", "chart": {**SPEC["marts"][0]["chart"], "name": "Spend by-Channel"}},
        ],
    }
    with pytest.raises(scaffold.ScaffoldError, match="both normalize to the filename"):
        scaffold.parse_spec(spec_dict)


def test_forced_rerun_removes_the_old_dashboard_when_product_display_name_changes(tmp_path: Path) -> None:
    scaffold.scaffold_product(scaffold.parse_spec(SPEC), tmp_path)
    old_dashboard = (
        tmp_path / "domains/logistics/reports/superset/route_efficiency/dashboards/Logistics_Route_Efficiency_1.yaml"
    )
    assert old_dashboard.exists()

    renamed_spec = {**SPEC, "product_display_name": "Logistics Route Cost Efficiency"}
    result = scaffold.scaffold_product(scaffold.parse_spec(renamed_spec), tmp_path, force=True)

    new_dashboard = (
        tmp_path
        / "domains/logistics/reports/superset/route_efficiency/dashboards"
        / "Logistics_Route_Cost_Efficiency_1.yaml"
    )
    assert not old_dashboard.exists()
    assert old_dashboard in result.removed
    assert new_dashboard.exists()
