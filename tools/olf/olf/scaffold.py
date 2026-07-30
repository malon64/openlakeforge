"""Golden-path product scaffolding.

Generates the complete product-owned layout for a new data product from a
declarative spec, so onboarding a product touches only `domains/<domain>/**`
and never shared Terraform or platform code. The generated shape mirrors the
seed products exactly; see `docs/product-onboarding.md`.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Every product bundle points at the one shared Superset Trino database, so this
# UUID must match the `uuid` in each existing databases/openlakeforge_trino.yaml.
TRINO_DATABASE_UUID = "8a87434c-559e-545d-badd-3575affe0185"

# check-structure.sh only accepts these Superset viz types.
ALLOWED_VIZ_TYPES = frozenset(
    {"echarts_timeseries_bar", "echarts_timeseries_line", "pie", "table"}
)

_UUID_NAMESPACE = uuid.UUID("6f6d1f9c-0f4a-5b7e-8c2d-1a2b3c4d5e6f")


class ScaffoldError(RuntimeError):
    pass


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    nullable: bool = False


@dataclass(frozen=True)
class Source:
    name: str
    description: str
    primary_key: tuple[str, ...]
    columns: tuple[Column, ...]
    example_rows: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class Metric:
    name: str
    expression: str
    verbose_name: str
    metric_type: str = "sum"


@dataclass(frozen=True)
class Chart:
    name: str
    description: str
    viz_type: str
    x_axis: str | None = None
    groupby: tuple[str, ...] = ()
    metrics: tuple[str, ...] = ()


@dataclass(frozen=True)
class Mart:
    name: str
    description: str
    sql: str
    columns: tuple[Column, ...]
    metrics: tuple[Metric, ...] = ()
    dttm_col: str | None = None
    chart: Chart | None = None


@dataclass(frozen=True)
class ProductSpec:
    domain: str
    domain_display_name: str
    domain_description: str
    owner: str
    product: str
    product_display_name: str
    product_description: str
    sources: tuple[Source, ...]
    marts: tuple[Mart, ...]
    domain_type: str = "Source-aligned"

    @property
    def asset_prefix(self) -> str:
        return f"{self.domain}_{self.product}"

    @property
    def silver_namespace(self) -> str:
        return f"{self.asset_prefix}_silver"

    @property
    def gold_namespace(self) -> str:
        return f"{self.asset_prefix}_gold"


@dataclass
class ScaffoldResult:
    written: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)


def _stable_uuid(*parts: str) -> str:
    return str(uuid.uuid5(_UUID_NAMESPACE, "/".join(parts)))


def _scalar(value: str) -> str:
    """Serialize a free-form string as a safe YAML scalar.

    Descriptions and display names are unrestricted user input. Interpolating
    them raw breaks the document (`Raw feed: daily exports`) or silently
    truncates it (`value # notes`), producing a tree that only fails later at
    `dbt parse` or Superset import.
    """
    return yaml.safe_dump(value, default_flow_style=True, width=10**6).strip().removesuffix("...").strip()


def _require(mapping: Mapping[str, Any], key: str, context: str) -> Any:
    if key not in mapping:
        raise ScaffoldError(f"{context}: missing required key {key!r}")
    return mapping[key]


def _columns(raw: Sequence[Mapping[str, Any]], context: str) -> tuple[Column, ...]:
    if not raw:
        raise ScaffoldError(f"{context}: at least one column is required")
    return tuple(
        Column(
            name=_require(entry, "name", context),
            type=_require(entry, "type", context),
            nullable=bool(entry.get("nullable", False)),
        )
        for entry in raw
    )


def load_spec(path: str | Path) -> ProductSpec:
    """Parse and validate a product scaffold spec."""
    source_path = Path(path)
    try:
        document = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ScaffoldError(f"{source_path}: could not read spec: {exc}") from exc
    if not isinstance(document, Mapping):
        raise ScaffoldError(f"{source_path}: spec must be a YAML mapping")
    return parse_spec(document, source=str(source_path))


def parse_spec(document: Mapping[str, Any], *, source: str = "spec") -> ProductSpec:
    domain = _require(document, "domain", source)
    product = _require(document, "product", source)

    sources: list[Source] = []
    for entry in _require(document, "sources", source):
        context = f"{source}: source {entry.get('name')!r}"
        sources.append(
            Source(
                name=_require(entry, "name", context),
                description=entry.get("description", ""),
                primary_key=tuple(_require(entry, "primary_key", context)),
                columns=_columns(_require(entry, "columns", context), context),
                example_rows=tuple(tuple(str(v) for v in row) for row in entry.get("example_rows", ())),
            )
        )
    if not sources:
        raise ScaffoldError(f"{source}: at least one source is required")

    marts: list[Mart] = []
    for entry in _require(document, "marts", source):
        context = f"{source}: mart {entry.get('name')!r}"
        chart_raw = entry.get("chart")
        chart = None
        if chart_raw:
            viz_type = chart_raw.get("viz_type", "echarts_timeseries_bar")
            if viz_type not in ALLOWED_VIZ_TYPES:
                raise ScaffoldError(
                    f"{context}: unsupported chart viz_type {viz_type!r}; "
                    f"expected one of {', '.join(sorted(ALLOWED_VIZ_TYPES))}"
                )
            chart = Chart(
                name=_require(chart_raw, "name", context),
                description=chart_raw.get("description", ""),
                viz_type=viz_type,
                x_axis=chart_raw.get("x_axis"),
                groupby=tuple(chart_raw.get("groupby", ())),
                metrics=tuple(chart_raw.get("metrics", ())),
            )
        marts.append(
            Mart(
                name=_require(entry, "name", context),
                description=entry.get("description", ""),
                sql=_require(entry, "sql", context),
                columns=_columns(_require(entry, "columns", context), context),
                metrics=tuple(
                    Metric(
                        name=_require(m, "name", context),
                        expression=_require(m, "expression", context),
                        verbose_name=m.get("verbose_name", m["name"]),
                        metric_type=m.get("metric_type", "sum"),
                    )
                    for m in entry.get("metrics", ())
                ),
                dttm_col=entry.get("dttm_col"),
                chart=chart,
            )
        )
    if not marts:
        raise ScaffoldError(f"{source}: at least one mart is required")

    spec = ProductSpec(
        domain=domain,
        domain_display_name=document.get("domain_display_name", domain.replace("_", " ").title()),
        domain_description=document.get("domain_description", f"{domain} domain."),
        owner=document.get("owner", domain.replace("_", "-")),
        product=product,
        product_display_name=document.get(
            "product_display_name", product.replace("_", " ").title()
        ),
        product_description=document.get("product_description", f"{product} data product."),
        sources=tuple(sources),
        marts=tuple(marts),
        domain_type=document.get("domain_type", "Source-aligned"),
    )
    _validate_chart_references(spec, source)
    return spec


def _validate_chart_references(spec: ProductSpec, source: str) -> None:
    """Reject charts referencing undeclared columns/metrics before writing files.

    check-structure.sh enforces the same rule against the generated bundle; failing
    here gives the author the error at scaffold time instead of at CI time.
    """
    for mart in spec.marts:
        if not mart.chart:
            continue
        columns = {column.name for column in mart.columns}
        metrics = {metric.name for metric in mart.metrics}
        referenced = [*(([mart.chart.x_axis]) if mart.chart.x_axis else []), *mart.chart.groupby]
        for name in referenced:
            if name not in columns:
                raise ScaffoldError(
                    f"{source}: chart {mart.chart.name!r} references column {name!r} "
                    f"which {mart.name} does not declare"
                )
        for name in mart.chart.metrics:
            if name not in metrics:
                raise ScaffoldError(
                    f"{source}: chart {mart.chart.name!r} references metric {name!r} "
                    f"which {mart.name} does not declare"
                )
        # Superset's pie viz reads a single scalar `metric`; extra entries would be
        # silently dropped at render time, so reject them here instead.
        if mart.chart.viz_type == "pie" and len(mart.chart.metrics) != 1:
            raise ScaffoldError(
                f"{source}: pie chart {mart.chart.name!r} must declare exactly one metric, "
                f"got {len(mart.chart.metrics)}"
            )
        if mart.dttm_col and mart.dttm_col not in columns:
            raise ScaffoldError(
                f"{source}: {mart.name} dttm_col {mart.dttm_col!r} is not a declared column"
            )


# --- Renderers ---------------------------------------------------------------


def _yaml_flow_columns(columns: Sequence[Column]) -> str:
    return "\n".join(
        f'        - {{ name: "{c.name}", type: "{c.type}", nullable: {str(c.nullable).lower()} }}'
        for c in columns
    )


def render_floe_contract(spec: ProductSpec) -> str:
    tags = f'["{spec.domain}", "{spec.product}", "silver"]'
    header = f'''version: "0.2"

metadata:
  project: "openlakeforge"
  owner: "{spec.owner}"
  description: {_scalar(f"{spec.product_display_name} Bronze to Silver Floe contracts.")}
  tags: {tags}

storages:
  default: "lakehouse_bronze"
  definitions:
    - name: "lakehouse_bronze"
      type: "s3"
      bucket: "{{{{OPENLAKEFORGE_STORAGE_BRONZE_BUCKET}}}}"
      region: "{{{{OPENLAKEFORGE_STORAGE_REGION}}}}"
    - name: "lakehouse_silver"
      type: "s3"
      bucket: "{{{{OPENLAKEFORGE_STORAGE_SILVER_BUCKET}}}}"
      region: "{{{{OPENLAKEFORGE_STORAGE_REGION}}}}"
    - name: "openlakeforge_ops"
      type: "s3"
      bucket: "{{{{OPENLAKEFORGE_OPS_BUCKET_NAME}}}}"
      region: "{{{{OPENLAKEFORGE_STORAGE_REGION}}}}"

report:
  path: "floe/reports/{spec.domain}/{spec.product}"
  storage: "openlakeforge_ops"

entities:
'''
    entities = []
    for src in spec.sources:
        path = f"{spec.domain}/{spec.product}/{src.name}"
        pk = ", ".join(f'"{key}"' for key in src.primary_key)
        entities.append(
            f'''  - name: "{src.name}"
    incremental_mode: "none"
    source:
      format: "csv"
      path: "{path}"
      storage: "lakehouse_bronze"
      options: {{ header: true, separator: ",", encoding: "utf-8", glob: "*.csv" }}
      cast_mode: "strict"
    sink:
      write_mode: "overwrite"
      accepted:
        format: "iceberg"
        path: "{path}"
        storage: "lakehouse_silver"
        iceberg: {{ catalog: "iceberg_catalog", namespace: "{spec.silver_namespace}", table: "{src.name}" }}
      rejected: {{ format: "csv", path: "floe/rejected/{path}", storage: "lakehouse_silver" }}
    policy: {{ severity: "reject" }}
    schema:
      normalize_columns: {{ enabled: true, strategy: "snake_case" }}
      primary_key: [{pk}]
      columns:
{_yaml_flow_columns(src.columns)}
'''
        )
    return header + "\n".join(entities)


def render_dlt_loader(spec: ProductSpec) -> str:
    const = f"{spec.product.upper()}_ENTITIES"
    entities = "\n".join(f'    "{src.name}",' for src in spec.sources)
    return f'''from __future__ import annotations

from pathlib import Path

from libs.bronze_csv import BronzeLoadResult, load_entities_to_bronze

{const} = (
{entities}
)

_DOMAIN_DIR = Path(__file__).resolve().parents[2]
_RAW_DIR = _DOMAIN_DIR / "examples" / "raw" / "{spec.product}"
_BRONZE_PREFIX = "{spec.domain}/{spec.product}"


def load_all_entities_to_bronze(raw_dir: Path | None = None) -> dict[str, BronzeLoadResult]:
    return load_entities_to_bronze(
        entities={const},
        raw_dir=raw_dir or _RAW_DIR,
        bronze_prefix=_BRONZE_PREFIX,
    )
'''


def render_dagster_pipeline(spec: ProductSpec) -> str:
    entities_const = f"{spec.product.upper()}_ENTITIES"
    gold_const = f"{spec.product.upper()}_GOLD_ASSETS"
    marts = "\n".join(f'    "{mart.name}",' for mart in spec.marts)
    return f'''from __future__ import annotations

from pathlib import Path

from libs.product_dagster import ProductDefinitionSpec, build_product_definitions

from domains.{spec.domain}.extract.dlt.{spec.product} import (
    {entities_const},
    load_all_entities_to_bronze,
)

_DOMAIN_DIR = Path(__file__).resolve().parents[2]

{gold_const} = (
{marts}
)

defs = build_product_definitions(
    ProductDefinitionSpec(
        domain="{spec.domain}",
        product="{spec.product}",
        asset_prefix="{spec.asset_prefix}",
        entities={entities_const},
        gold_assets={gold_const},
        domain_dir=_DOMAIN_DIR,
        bronze_loader=load_all_entities_to_bronze,
    )
)
'''


def render_definitions(products: Sequence[str], domain: str) -> str:
    imports = ", ".join(sorted(products))
    merged = "\n".join(f"    {name}.defs," for name in sorted(products))
    return f'''from dagster import Definitions

from domains.{domain}.pipelines.dagster import {imports}


defs = Definitions.merge(
{merged}
)
'''


def render_dbt_project(spec: ProductSpec) -> str:
    return f'''name: {spec.asset_prefix}
version: "1.0.0"
config-version: 2

profile: {spec.asset_prefix}

model-paths: ["models"]
macro-paths: []
target-path: "target"
clean-targets: ["target", "dbt_packages"]

models:
  {spec.asset_prefix}:
    +database: "{{{{ env_var('OPENLAKEFORGE_CATALOG_NAME', 'lakehouse_dev') }}}}"
    +materialized: table
    +on_table_exists: replace
    +meta:
      dagster:
        group: {spec.asset_prefix}
    gold:
      +tags: ["{spec.domain}", "{spec.product}", "gold"]
'''


def render_dbt_profiles(spec: ProductSpec) -> str:
    target = f'''      type: trino
      method: none
      host: "{{{{ env_var('OPENLAKEFORGE_QUERY_TRINO_HOST', 'trino') }}}}"
      port: "{{{{ env_var('OPENLAKEFORGE_QUERY_TRINO_PORT', '8080') | int }}}}"
      user: "{{{{ env_var('OPENLAKEFORGE_DBT_TRINO_USER', 'openlakeforge-dbt') }}}}"
      database: "{{{{ env_var('OPENLAKEFORGE_CATALOG_NAME', 'lakehouse_dev') }}}}"
      schema: {spec.gold_namespace}
      threads: 1'''
    return f'''{spec.asset_prefix}:
  target: local
  outputs:
    local:
{target}
    local_runtime:
{target}
'''


def render_dbt_sources(spec: ProductSpec) -> str:
    tables = "\n".join(
        f"      - name: {src.name}\n        description: {_scalar(src.description or src.name)}"
        for src in spec.sources
    )
    return f'''version: 2

sources:
  - name: silver
    database: "{{{{ env_var('OPENLAKEFORGE_CATALOG_NAME', 'lakehouse_dev') }}}}"
    schema: {spec.silver_namespace}
    description: Floe-owned {spec.product_display_name} Silver Iceberg tables.
    tables:
{tables}
'''


def render_dbt_gold_schema(spec: ProductSpec) -> str:
    models = "\n".join(
        f"  - name: {mart.name}\n    description: {_scalar(mart.description or mart.name)}"
        for mart in spec.marts
    )
    return f"version: 2\n\nmodels:\n{models}\n"


def render_domain_descriptor(spec: ProductSpec, existing: Mapping[str, Any] | None = None) -> str:
    """Render a domain descriptor, appending the product when the domain exists."""
    product_entry: dict[str, Any] = {
        "id": spec.product,
        "name": spec.asset_prefix,
        "displayName": spec.product_display_name,
        "description": spec.product_description,
        "status": "planned",
        "asset_prefix": spec.asset_prefix,
        "bronze": [
            {
                "name": src.name,
                "path": f"s3://lakehouse-bronze/{spec.domain}/{spec.product}/{src.name}",
                "description": src.description or f"Raw CSV {src.name}.",
            }
            for src in spec.sources
        ],
        "silver_tables": {
            "tables": [
                {"name": src.name, "description": f"Validated {src.name}."} for src in spec.sources
            ]
        },
        "gold_tables": {
            "tables": [
                {"name": mart.name, "description": mart.description or mart.name}
                for mart in spec.marts
            ]
        },
    }

    if existing:
        document = dict(existing)
        products = [p for p in document.get("data_products", []) if p.get("id") != spec.product]
        document["data_products"] = [*products, product_entry]
    else:
        document = {
            "apiVersion": "openlakeforge.io/v1alpha1",
            "kind": "Domain",
            "name": spec.domain,
            "displayName": spec.domain_display_name,
            "domainType": spec.domain_type,
            "description": spec.domain_description,
            "status": "planned",
            "owners": [],
            "medallion": {
                "bronze": {"owner": "ingestion", "description": "Raw immutable landing zone."},
                "silver": {"owner": "floe", "description": "Technically validated Iceberg tables."},
                "gold": {"owner": "dbt", "description": "Business-ready marts and analytics models."},
            },
            "data_products": [product_entry],
        }

    return yaml.safe_dump(document, sort_keys=False, default_flow_style=False, width=100)


def render_superset_dataset(spec: ProductSpec, mart: Mart) -> str:
    dataset_uuid = _stable_uuid("dataset", spec.gold_namespace, mart.name)
    metrics = "".join(
        f"""  - metric_name: {metric.name}
    verbose_name: {metric.verbose_name}
    metric_type: {metric.metric_type}
    expression: {metric.expression}
"""
        for metric in mart.metrics
    )
    columns = "".join(
        f"""  - column_name: {column.name}
    type: {_superset_type(column.type)}
{_dttm_line(column, mart)}    groupby: {str(_groupby(column)).lower()}
    filterable: true
"""
        for column in mart.columns
    )
    return f"""table_name: {mart.name}
main_dttm_col: {mart.dttm_col or "null"}
description: {_scalar(mart.description or mart.name)}
schema: {spec.gold_namespace}
uuid: {dataset_uuid}
metrics:
{metrics or "  []"}columns:
{columns}version: 1.0.0
database_uuid: {TRINO_DATABASE_UUID}
"""


def _superset_type(column_type: str) -> str:
    return {
        "string": "VARCHAR",
        "integer": "INTEGER",
        "long": "BIGINT",
        "double": "DOUBLE",
        "decimal": "DECIMAL",
        "date": "DATE",
        "timestamp": "TIMESTAMP",
        "boolean": "BOOLEAN",
    }.get(column_type, "VARCHAR")


def _dttm_line(column: Column, mart: Mart) -> str:
    return "    is_dttm: true\n" if column.name == mart.dttm_col else ""


def _groupby(column: Column) -> bool:
    return _superset_type(column.type) not in {"INTEGER", "BIGINT", "DOUBLE", "DECIMAL"}


def _chart_params(chart: Chart) -> dict[str, Any]:
    """Build the params block Superset actually reads for this viz type.

    The viz types do not share a parameter shape. Pie reads a singular scalar
    `metric` and ignores `metrics`/`x_axis`; table wants `query_mode: aggregate`
    and has no axis or legend; only the echarts timeseries types use `x_axis` and
    `y_axis_format`. Emitting one template for all four produced charts that
    imported cleanly but rendered empty, because the metric never reached the
    query. `check-structure.sh` accepts either `metric` or `metrics`, so it did
    not catch this.
    """
    params: dict[str, Any] = {"datasource": None, "viz_type": chart.viz_type}
    groupby = list(chart.groupby)
    metrics = list(chart.metrics)

    if chart.viz_type == "pie":
        params["groupby"] = groupby
        # Pie is single-metric; a spec listing more than one is rejected upstream.
        params["metric"] = metrics[0] if metrics else None
        params.update(
            {
                "row_limit": 100,
                "number_format": "SMART_NUMBER",
                "show_labels": True,
                "show_legend": True,
                "color_scheme": "supersetColors",
            }
        )
    elif chart.viz_type == "table":
        params["query_mode"] = "aggregate"
        params["groupby"] = groupby
        params["metrics"] = metrics
        params.update(
            {
                "row_limit": 100,
                "order_desc": True,
                "show_cell_bars": True,
                "table_timestamp_format": "smart_date",
            }
        )
    else:  # echarts_timeseries_bar / echarts_timeseries_line
        params["x_axis"] = chart.x_axis
        params["metrics"] = metrics
        params["groupby"] = groupby
        params.update(
            {
                "row_limit": 100,
                "color_scheme": "supersetColors",
                "show_legend": True,
                "y_axis_format": "SMART_NUMBER",
            }
        )
    return params


def render_superset_chart(spec: ProductSpec, mart: Mart) -> str:
    chart = mart.chart
    assert chart is not None
    dataset_uuid = _stable_uuid("dataset", spec.gold_namespace, mart.name)
    chart_uuid = _stable_uuid("chart", spec.gold_namespace, chart.name)
    params_yaml = yaml.safe_dump(
        {"params": _chart_params(chart)}, sort_keys=False, default_flow_style=False, width=1000
    ).rstrip("\n")
    return f"""slice_name: {_scalar(chart.name)}
description: {_scalar(chart.description or chart.name)}
viz_type: {chart.viz_type}
{params_yaml}
query_context: null
cache_timeout: null
uuid: {chart_uuid}
version: 1.0.0
dataset_uuid: {dataset_uuid}
"""


def render_superset_dashboard(spec: ProductSpec) -> str:
    title = spec.product_display_name
    slug = title.lower().replace("_", "-").replace(" ", "-")
    dashboard_uuid = _stable_uuid("dashboard", spec.gold_namespace, title)
    charted = [mart for mart in spec.marts if mart.chart]

    chart_ids = [f"CHART-{index}" for index, _ in enumerate(charted, start=1)]
    blocks = []
    for index, (block_id, mart) in enumerate(zip(chart_ids, charted, strict=True), start=1):
        chart = mart.chart
        assert chart is not None
        blocks.append(
            f"""  {block_id}:
    children: []
    id: {block_id}
    meta:
      chartId: {index}
      height: 50
      sliceName: {chart.name}
      uuid: {_stable_uuid("chart", spec.gold_namespace, chart.name)}
      width: {12 // max(len(charted), 1)}
    parents: [ROOT_ID, GRID_ID, ROW-1]
    type: CHART
"""
        )
    return f"""dashboard_title: {_scalar(title)}
description: {_scalar(spec.product_description)}
css: ''
slug: {slug}
published: true
uuid: {dashboard_uuid}
position:
  DASHBOARD_VERSION_KEY: v2
  ROOT_ID: {{ children: [GRID_ID], id: ROOT_ID, type: ROOT }}
  GRID_ID:
    children: [ROW-1]
    id: GRID_ID
    parents: [ROOT_ID]
    type: GRID
  ROW-1:
    children: [{", ".join(chart_ids)}]
    id: ROW-1
    meta: {{ background: BACKGROUND_TRANSPARENT }}
    parents: [ROOT_ID, GRID_ID]
    type: ROW
{"".join(blocks)}metadata:
  timed_refresh_immune_slices: []
  expanded_slices: {{}}
  refresh_frequency: 0
  color_scheme: supersetColors
  label_colors: {{}}
  shared_label_colors: []
  map_label_colors: {{}}
  cross_filters_enabled: true
  native_filter_configuration: []
version: 1.0.0
"""


def render_superset_database() -> str:
    return f"""database_name: OpenLakeForge Trino
sqlalchemy_uri: trino://superset@trino:8080/iceberg
cache_timeout: null
expose_in_sqllab: true
allow_run_async: false
allow_ctas: false
allow_cvas: false
allow_dml: false
allow_file_upload: false
extra:
  metadata_params: {{}}
  engine_params: {{}}
  metadata_cache_timeout: {{}}
  schemas_allowed_for_file_upload: []
impersonate_user: false
uuid: {TRINO_DATABASE_UUID}
version: 1.0.0
"""


# --- Scaffolding -------------------------------------------------------------


def _write(path: Path, content: str, *, force: bool, result: ScaffoldResult) -> None:
    if path.exists() and not force:
        result.skipped.append(path)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    result.written.append(path)


def scaffold_product(spec: ProductSpec, repo_root: str | Path, *, force: bool = False) -> ScaffoldResult:
    """Generate every product-owned file for `spec` under `repo_root/domains`."""
    root = Path(repo_root)
    domain_dir = root / "domains" / spec.domain
    result = ScaffoldResult()

    existing_descriptor: Mapping[str, Any] | None = None
    descriptor_path = domain_dir / "domain.yaml"
    if descriptor_path.exists():
        existing_descriptor = yaml.safe_load(descriptor_path.read_text(encoding="utf-8"))

    # The descriptor is the discovery source of truth, so it is always rewritten.
    _write(descriptor_path, render_domain_descriptor(spec, existing_descriptor), force=True, result=result)

    _write(domain_dir / "__init__.py", "", force=force, result=result)
    _write(
        domain_dir / "README.md",
        f"# {spec.domain_display_name}\n\n{spec.domain_description}\n",
        force=force,
        result=result,
    )
    for package in ("extract", "extract/dlt", "pipelines", "pipelines/dagster"):
        _write(domain_dir / package / "__init__.py", "", force=force, result=result)

    _write(
        domain_dir / "contracts" / "floe" / f"{spec.product}.yml",
        render_floe_contract(spec),
        force=force,
        result=result,
    )
    _write(
        domain_dir / "extract" / "dlt" / f"{spec.product}.py",
        render_dlt_loader(spec),
        force=force,
        result=result,
    )
    _write(
        domain_dir / "pipelines" / "dagster" / f"{spec.product}.py",
        render_dagster_pipeline(spec),
        force=force,
        result=result,
    )

    products = sorted(
        {path.stem for path in (domain_dir / "pipelines" / "dagster").glob("*.py") if path.stem != "__init__"}
        | {spec.product}
    )
    _write(domain_dir / "definitions.py", render_definitions(products, spec.domain), force=True, result=result)

    dbt_dir = domain_dir / "transformations" / "dbt" / spec.product
    _write(dbt_dir / "dbt_project.yml", render_dbt_project(spec), force=force, result=result)
    _write(
        dbt_dir / "packages.yml",
        "packages:\n  - local: ../../../../../libs/dbt/openlakeforge_dbt\n",
        force=force,
        result=result,
    )
    _write(dbt_dir / "profiles.yml", render_dbt_profiles(spec), force=force, result=result)
    _write(dbt_dir / "models" / "sources.yml", render_dbt_sources(spec), force=force, result=result)
    _write(dbt_dir / "models" / "gold" / "schema.yml", render_dbt_gold_schema(spec), force=force, result=result)
    for mart in spec.marts:
        _write(
            dbt_dir / "models" / "gold" / f"{mart.name}.sql",
            mart.sql if mart.sql.endswith("\n") else mart.sql + "\n",
            force=force,
            result=result,
        )

    raw_dir = domain_dir / "examples" / "raw" / spec.product
    for src in spec.sources:
        header = ",".join(column.name for column in src.columns)
        rows = "\n".join(",".join(row) for row in src.example_rows)
        csv_text = f"{header}\n{rows}\n" if rows else f"{header}\n"
        _write(raw_dir / f"{src.name}.csv", csv_text, force=force, result=result)

    reports_dir = domain_dir / "reports" / "superset" / spec.product
    _write(
        reports_dir / "metadata.yaml",
        "version: 1.0.0\ntype: assets\ntimestamp: '2026-06-11T00:00:00+00:00'\n",
        force=force,
        result=result,
    )
    _write(
        reports_dir / "databases" / "openlakeforge_trino.yaml",
        render_superset_database(),
        force=force,
        result=result,
    )
    for mart in spec.marts:
        _write(
            reports_dir / "datasets" / "OpenLakeForge_Trino" / f"{mart.name}.yaml",
            render_superset_dataset(spec, mart),
            force=force,
            result=result,
        )
        if mart.chart:
            chart_file = mart.chart.name.replace(" ", "_").replace("-", "_")
            _write(
                reports_dir / "charts" / f"{chart_file}_1.yaml",
                render_superset_chart(spec, mart),
                force=force,
                result=result,
            )
    dashboard_file = spec.product_display_name.replace(" ", "_").replace("-", "_")
    _write(
        reports_dir / "dashboards" / f"{dashboard_file}_1.yaml",
        render_superset_dashboard(spec),
        force=force,
        result=result,
    )
    _write(
        reports_dir / "README.md",
        f"# {spec.product_display_name} Superset report bundle\n\n{spec.product_description}\n",
        force=force,
        result=result,
    )

    return result
