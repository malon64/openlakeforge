"""Golden-path product scaffolding.

Generates the complete product-owned layout for a new data product from a
declarative spec, so onboarding a product touches only `domains/<domain>/**`
and never shared Terraform or platform code. The generated shape mirrors the
seed products exactly; see `docs/product-onboarding.md`.
"""

from __future__ import annotations

import csv
import io
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from olf.descriptors import slugify

# Matches the domain.yaml `name` convention enforced in descriptors.py. domain
# and product are both used as filesystem path components (domains/<domain>/...)
# and as Python module segments (domains.<domain>.definitions), so anything
# outside this pattern -- hyphens, uppercase, path separators, ".." -- would
# either produce a broken import or let a crafted spec write outside
# domains/<domain>/**.
_IDENTIFIER_PATTERN = re.compile(r"[a-z][a-z0-9_]*")

# Every product bundle points at the one shared Superset Trino database, so this
# UUID must match the `uuid` in each existing databases/openlakeforge_trino.yaml.
TRINO_DATABASE_UUID = "8a87434c-559e-545d-badd-3575affe0185"

# check-structure.sh only accepts these Superset viz types.
ALLOWED_VIZ_TYPES = frozenset(
    {"echarts_timeseries_bar", "echarts_timeseries_line", "pie", "table"}
)

# Must match _superset_type's mapping exactly: that function silently falls
# back to VARCHAR for anything not listed there, so an unvalidated type here
# (e.g. a typo'd "timestmp") would write into the Floe contract unchanged
# while the Superset dataset silently represents the same column as VARCHAR.
ALLOWED_COLUMN_TYPES = frozenset(
    {"string", "integer", "long", "double", "decimal", "date", "timestamp", "boolean"}
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
    removed: list[Path] = field(default_factory=list)


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


def _require_identifier(mapping: Mapping[str, Any], key: str, context: str) -> str:
    value = _require(mapping, key, context)
    if not isinstance(value, str) or not _IDENTIFIER_PATTERN.fullmatch(value):
        raise ScaffoldError(
            f"{context}: {key} {value!r} must match '^[a-z][a-z0-9_]*$' "
            "(it is used as both a domains/<...>/ path component and a Python module name)"
        )
    return value


def _check_string(value: Any, *, key: str, context: str, allow_empty: bool = True) -> str:
    """Require a plain string (optionally non-empty).

    A non-string value like `product_description: 2026` parses fine here and
    scaffolds successfully, but descriptors.validate_domain_descriptor
    requires descriptions (and, non-empty, displayName) to be strings -- so
    descriptor discovery, and everything built on it (check-structure, e2e),
    then rejects the scaffold's own output.
    """
    if not isinstance(value, str) or (not allow_empty and not value):
        expected = "a non-empty string" if not allow_empty else "a string"
        raise ScaffoldError(f"{context}: {key} must be {expected}")
    return value


def _check_safe_filename_value(value: Any, *, key: str, context: str) -> str:
    """Reject a value that could escape the product's own directory tree when
    concatenated into a filename.

    Source, mart, chart, and product display names all get interpolated
    straight into a Path somewhere (e.g. `raw_dir / f"{src.name}.csv"`,
    `reports_dir / "dashboards" / f"{dashboard_file}_1.yaml"`). A spec that
    parses cleanly could set one to `../../../../../escaped`, which `_write`
    would then happily write outside `domains/<domain>/**` while scaffolding
    still reports success.
    """
    if not isinstance(value, str) or not value.strip():
        raise ScaffoldError(f"{context}: {key} must be a non-empty string")
    if "/" in value or "\\" in value or "\x00" in value:
        raise ScaffoldError(
            f"{context}: {key} {value!r} must not contain a path separator "
            "(it is used to derive a filename)"
        )
    if value in {".", ".."} or value.startswith("."):
        raise ScaffoldError(f"{context}: {key} {value!r} must not start with '.'")
    return value


def _require_safe_filename_component(mapping: Mapping[str, Any], key: str, context: str) -> str:
    return _check_safe_filename_value(_require(mapping, key, context), key=key, context=context)


def _columns(raw: Sequence[Mapping[str, Any]], context: str) -> tuple[Column, ...]:
    if not raw:
        raise ScaffoldError(f"{context}: at least one column is required")
    columns = []
    seen_names: set[str] = set()
    for entry in raw:
        column_type = _require(entry, "type", context)
        if column_type not in ALLOWED_COLUMN_TYPES:
            raise ScaffoldError(
                f"{context}: unsupported column type {column_type!r}; "
                f"expected one of {', '.join(sorted(ALLOWED_COLUMN_TYPES))}"
            )
        nullable = entry.get("nullable", False)
        # bool("false") is True: a quoted or templated "false" would silently
        # weaken the generated Floe schema to nullable instead of being
        # rejected, since any nonempty string is truthy.
        if not isinstance(nullable, bool):
            raise ScaffoldError(f"{context}: nullable {nullable!r} must be a boolean")
        # Renderers interpolate this both inside a double-quoted YAML
        # scalar (Floe contract) and completely unquoted (Superset
        # column_name), so a name containing '"' or ': ' breaks the
        # generated file instead of just being ugly.
        name = _require_identifier(entry, "name", context)
        if name in seen_names:
            raise ScaffoldError(f"{context}: duplicate column name {name!r}")
        seen_names.add(name)
        columns.append(Column(name=name, type=column_type, nullable=nullable))
    return tuple(columns)


def _primary_key(
    raw: Any, columns: tuple[Column, ...], context: str
) -> tuple[str, ...]:
    """Validate `primary_key` as a non-empty list of declared column names.

    A bare string (`primary_key: customer_id`) is a `Sequence[str]` too, so
    `tuple(raw)` would silently split it into one "key" per character instead
    of raising; a typo'd key would otherwise pass through and end up in the
    generated Floe contract declaring a primary key column that doesn't exist.
    """
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or not raw:
        raise ScaffoldError(f"{context}: primary_key must be a non-empty list of column names")
    if not all(isinstance(key, str) for key in raw):
        raise ScaffoldError(f"{context}: primary_key entries must all be strings")
    column_names = {column.name for column in columns}
    unknown = [key for key in raw if key not in column_names]
    if unknown:
        raise ScaffoldError(f"{context}: primary_key {unknown} not declared in columns")
    return tuple(raw)


def _metrics(raw: Sequence[Mapping[str, Any]], context: str) -> tuple[Metric, ...]:
    """Build a mart's metrics, rejecting a duplicate `name`.

    Superset treats metric_name as a dataset-local identity; two metrics with
    the same name produce a dataset with duplicate metric_name entries, which
    can fail import or leave chart references to that name ambiguous.
    check-structure.sh converts names to a set and so cannot catch this.
    """
    metrics = []
    seen_names: set[str] = set()
    for m in raw:
        name = _require(m, "name", context)
        if name in seen_names:
            raise ScaffoldError(f"{context}: duplicate metric name {name!r}")
        seen_names.add(name)
        metrics.append(
            Metric(
                name=name,
                expression=_require(m, "expression", context),
                verbose_name=m.get("verbose_name", name),
                metric_type=m.get("metric_type", "sum"),
            )
        )
    return tuple(metrics)


def _example_rows(raw: Any, context: str) -> tuple[tuple[str, ...], ...]:
    """Require at least one example row.

    A source with no example rows scaffolds a header-only CSV.
    `libs/bronze_csv.py::load_entity_to_bronze` explicitly raises "did not
    produce any rows" for that file, so the scaffold's own golden-path
    pipeline can never run -- scaffolding reported success for a product
    that cannot execute.
    """
    if not raw:
        raise ScaffoldError(f"{context}: at least one example_rows entry is required")
    return tuple(tuple("" if v is None else str(v) for v in row) for row in raw)


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
    domain = _require_identifier(document, "domain", source)
    product = _require_identifier(document, "product", source)

    sources: list[Source] = []
    for entry in _require(document, "sources", source):
        context = f"{source}: source {entry.get('name')!r}"
        columns = _columns(_require(entry, "columns", context), context)
        sources.append(
            Source(
                name=_require_identifier(entry, "name", context),
                description=_check_string(entry.get("description", ""), key="description", context=context),
                primary_key=_primary_key(_require(entry, "primary_key", context), columns, context),
                columns=columns,
                example_rows=_example_rows(entry.get("example_rows", ()), context),
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
                name=_require_safe_filename_component(chart_raw, "name", context),
                description=chart_raw.get("description", ""),
                viz_type=viz_type,
                x_axis=chart_raw.get("x_axis"),
                groupby=tuple(chart_raw.get("groupby", ())),
                metrics=tuple(chart_raw.get("metrics", ())),
            )
        marts.append(
            Mart(
                name=_require_identifier(entry, "name", context),
                description=_check_string(entry.get("description", ""), key="description", context=context),
                sql=_require(entry, "sql", context),
                columns=_columns(_require(entry, "columns", context), context),
                metrics=_metrics(entry.get("metrics", ()), context),
                dttm_col=entry.get("dttm_col"),
                chart=chart,
            )
        )
    if not marts:
        raise ScaffoldError(f"{source}: at least one mart is required")

    spec = ProductSpec(
        domain=domain,
        domain_display_name=_check_string(
            document.get("domain_display_name", domain.replace("_", " ").title()),
            key="domain_display_name",
            context=source,
            allow_empty=False,
        ),
        domain_description=_check_string(
            document.get("domain_description", f"{domain} domain."),
            key="domain_description",
            context=source,
        ),
        owner=document.get("owner", domain.replace("_", "-")),
        product=product,
        product_display_name=_check_safe_filename_value(
            document.get("product_display_name", product.replace("_", " ").title()),
            key="product_display_name",
            context=source,
        ),
        product_description=_check_string(
            document.get("product_description", f"{product} data product."),
            key="product_description",
            context=source,
        ),
        sources=tuple(sources),
        marts=tuple(marts),
        domain_type=document.get("domain_type", "Source-aligned"),
    )
    _validate_chart_references(spec, source)
    _validate_chart_filename_collisions(spec, source)
    return spec


def _validate_chart_filename_collisions(spec: ProductSpec, source: str) -> None:
    """Reject two distinct chart names that normalize to the same filename.

    Chart filenames are derived by replacing spaces and dashes with
    underscores (e.g. "Spend-by Channel" and "Spend by-Channel" both become
    "Spend_by_Channel"). On first scaffold the second chart is silently
    skipped; under --force it overwrites the first -- either way the
    dashboard ends up referencing two distinct chart UUIDs while the report
    bundle contains only one chart asset.
    """
    seen: dict[str, str] = {}
    for mart in spec.marts:
        if not mart.chart:
            continue
        chart_file = mart.chart.name.replace(" ", "_").replace("-", "_")
        if chart_file in seen:
            raise ScaffoldError(
                f"{source}: charts {seen[chart_file]!r} and {mart.chart.name!r} both normalize to the "
                f"filename {chart_file!r} -- rename one of them"
            )
        seen[chart_file] = mart.chart.name


def _validate_chart_references(spec: ProductSpec, source: str) -> None:
    """Reject charts referencing undeclared columns/metrics before writing files.

    check-structure.sh enforces the same rule against the generated bundle; failing
    here gives the author the error at scaffold time instead of at CI time.
    """
    for mart in spec.marts:
        columns = {column.name for column in mart.columns}
        # Validated unconditionally, not only for marts with a chart: dttm_col
        # is emitted as the Superset dataset's main_dttm_col regardless of
        # whether a chart exists, so a typo or undeclared column would
        # otherwise bypass validation entirely for chartless (e.g.
        # dimension-only) marts and fail import or temporal queries instead.
        if mart.dttm_col and mart.dttm_col not in columns:
            raise ScaffoldError(
                f"{source}: {mart.name} dttm_col {mart.dttm_col!r} is not a declared column"
            )
        if not mart.chart:
            continue
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
        # A time-series chart with no x_axis or no metrics renders with a null
        # temporal axis and/or an empty series list. The chart bundle still
        # imports -- structure checks only validate fields that are present --
        # but Superset has nothing to plot, so the generated chart is unusable.
        if mart.chart.viz_type in ("echarts_timeseries_bar", "echarts_timeseries_line"):
            if not mart.chart.x_axis:
                raise ScaffoldError(
                    f"{source}: chart {mart.chart.name!r} ({mart.chart.viz_type}) requires x_axis"
                )
            if not mart.chart.metrics:
                raise ScaffoldError(
                    f"{source}: chart {mart.chart.name!r} ({mart.chart.viz_type}) requires at least one metric"
                )
        # table renders query_mode: aggregate with whatever groupby/metrics are
        # given; with neither, the query has no dimension or measure to select
        # and the imported chart has nothing to show.
        if mart.chart.viz_type == "table" and not mart.chart.groupby and not mart.chart.metrics:
            raise ScaffoldError(
                f"{source}: table chart {mart.chart.name!r} requires at least one groupby column or metric"
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
  owner: {_scalar(spec.owner)}
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
    description: {_scalar(f"Floe-owned {spec.product_display_name} Silver Iceberg tables.")}
    tables:
{tables}
'''


def render_dbt_gold_schema(spec: ProductSpec) -> str:
    models = "\n".join(
        f"  - name: {mart.name}\n    description: {_scalar(mart.description or mart.name)}"
        for mart in spec.marts
    )
    return f"version: 2\n\nmodels:\n{models}\n"


def render_domain_descriptor(
    spec: ProductSpec,
    existing: Mapping[str, Any] | None = None,
    *,
    product_entry_override: Mapping[str, Any] | None = None,
) -> str:
    """Render a domain descriptor, appending the product when the domain exists.

    `product_entry_override`, when given, is written verbatim instead of a
    fresh entry built from `spec` -- see the caller in `scaffold_product` for
    why: rerunning without --force skips this product's actual files, so its
    descriptor entry must not drift to describe sources/marts nothing on disk
    implements.
    """
    product_entry: dict[str, Any] = (
        dict(product_entry_override)
        if product_entry_override is not None
        else {
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
    )

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
        f"""  - metric_name: {_scalar(metric.name)}
    verbose_name: {_scalar(metric.verbose_name)}
    metric_type: {_scalar(metric.metric_type)}
    expression: {_scalar(metric.expression)}
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
{metrics or "  []\n"}columns:
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
    # Shared with descriptors.slugify: descriptor discovery re-derives this
    # same slug from the display name, so any divergence makes the E2E suite
    # report the scaffolded dashboard as missing.
    slug = slugify(title)
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
      sliceName: {_scalar(chart.name)}
      uuid: {_stable_uuid("chart", spec.gold_namespace, chart.name)}
      width: {max(12 // max(len(charted), 1), 1)}
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


def _render_csv(header: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    """Render example CSV rows with proper quoting.

    A raw ",".join() breaks on any value containing a comma, quote, or
    newline (e.g. a customer name "Smith, Inc."): the row splits into the
    wrong number of fields, and the strict Floe ingestion this scaffold
    generates rejects or misreads it.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue()


def _remove_stale(directory: Path, *, glob: str, keep: set[str], result: ScaffoldResult) -> None:
    """Delete generated files under `directory` matching `glob` that a mart or
    chart removed/renamed from the spec left behind.

    Only called under --force. Writing only the marts/charts in the new spec
    (never deleting one that dropped out) leaves a stale `.sql` model or
    Superset dataset/chart on disk that dbt/Superset still discovers, while
    domain.yaml and the descriptor-derived e2e table count reflect only the
    new set -- an exact-count mismatch.
    """
    if not directory.is_dir():
        return
    for existing in sorted(directory.glob(glob)):
        if existing.name not in keep:
            existing.unlink()
            result.removed.append(existing)


def _product_owned_paths(domain_dir: Path, spec: ProductSpec) -> list[Path]:
    """Every path `scaffold_product` writes for this product.

    Mirrors the `_write` calls below (mart model/dataset filenames, chart
    filenames, the dashboard filename derived from product_display_name) so
    an unforced rerun of an existing product can check whether *every* one
    of them already exists, not just source/mart names -- a product_display_name
    or chart rename derives a filename that doesn't exist yet either, and
    `_write` creates it regardless of `force` just like a new mart would.
    """
    dbt_dir = domain_dir / "transformations" / "dbt" / spec.product
    raw_dir = domain_dir / "examples" / "raw" / spec.product
    reports_dir = domain_dir / "reports" / "superset" / spec.product
    dashboard_file = spec.product_display_name.replace(" ", "_").replace("-", "_")
    paths = [
        domain_dir / "contracts" / "floe" / f"{spec.product}.yml",
        domain_dir / "extract" / "dlt" / f"{spec.product}.py",
        domain_dir / "pipelines" / "dagster" / f"{spec.product}.py",
        dbt_dir / "dbt_project.yml",
        dbt_dir / "packages.yml",
        dbt_dir / "profiles.yml",
        dbt_dir / "models" / "sources.yml",
        dbt_dir / "models" / "gold" / "schema.yml",
        *(dbt_dir / "models" / "gold" / f"{mart.name}.sql" for mart in spec.marts),
        *(raw_dir / f"{src.name}.csv" for src in spec.sources),
        reports_dir / "metadata.yaml",
        reports_dir / "databases" / "openlakeforge_trino.yaml",
        *(
            reports_dir / "datasets" / "OpenLakeForge_Trino" / f"{mart.name}.yaml"
            for mart in spec.marts
        ),
        *(
            reports_dir / "charts" / f"{mart.chart.name.replace(' ', '_').replace('-', '_')}_1.yaml"
            for mart in spec.marts
            if mart.chart
        ),
        reports_dir / "dashboards" / f"{dashboard_file}_1.yaml",
        reports_dir / "README.md",
    ]
    return paths


def scaffold_product(spec: ProductSpec, repo_root: str | Path, *, force: bool = False) -> ScaffoldResult:
    """Generate every product-owned file for `spec` under `repo_root/domains`."""
    root = Path(repo_root)
    domain_dir = root / "domains" / spec.domain
    result = ScaffoldResult()

    existing_descriptor: Mapping[str, Any] | None = None
    descriptor_path = domain_dir / "domain.yaml"
    if descriptor_path.exists():
        existing_descriptor = yaml.safe_load(descriptor_path.read_text(encoding="utf-8"))

    # The descriptor is the discovery source of truth, so it is always rewritten
    # -- except this product's own entry, when its actual files are about to be
    # skipped below (force=False and the product already exists). Rewriting the
    # entry from `spec` in that case would make discovery/Terraform expect
    # sources or marts that the retained contract, dbt models, and datasets do
    # not implement, since those writes are skipped. Preserve the entry that
    # matches what is actually on disk instead; rerun with --force to
    # regenerate the product's files and its descriptor entry together.
    existing_product_entry = next(
        (
            p
            for p in (existing_descriptor or {}).get("data_products", [])
            if p.get("id") == spec.product
        ),
        None,
    )
    if existing_product_entry is not None and not force:
        # The per-file `force` check below only protects files that already
        # exist: any path this spec would write that doesn't exist yet gets
        # created regardless of `force` -- not just a newly added mart or
        # source, but also a renamed chart or a changed product_display_name,
        # both of which derive a filename nothing on disk has yet. dbt/Superset
        # then discover an extra or duplicate model/dataset/chart/dashboard
        # that domain.yaml and the descriptor-derived E2E count don't know
        # about. Refuse the whole rerun instead of partially applying it.
        missing = [path for path in _product_owned_paths(domain_dir, spec) if not path.exists()]
        if missing:
            raise ScaffoldError(
                f"{spec.domain}/{spec.product}: spec no longer matches what is already "
                "scaffolded on disk (missing " + ", ".join(str(p) for p in missing) + "); "
                "rerun with --force to regenerate this product's files"
            )
    product_entry_override = existing_product_entry if existing_product_entry is not None and not force else None
    _write(
        descriptor_path,
        render_domain_descriptor(spec, existing_descriptor, product_entry_override=product_entry_override),
        force=True,
        result=result,
    )

    # Domain-level files shared by every product in this domain. --force on
    # one product's spec must not regenerate them: forcing a Sales rerun
    # while a Marketing product also lives in this domain would otherwise
    # erase the domain's actual README/docstrings, which are unrelated to
    # the product being forced. force=False here still creates them on a
    # brand-new domain (the path doesn't exist yet) but never overwrites an
    # existing one.
    _write(domain_dir / "__init__.py", "", force=False, result=result)
    _write(
        domain_dir / "README.md",
        f"# {spec.domain_display_name}\n\n{spec.domain_description}\n",
        force=False,
        result=result,
    )
    for package in ("extract", "extract/dlt", "pipelines", "pipelines/dagster"):
        _write(domain_dir / package / "__init__.py", "", force=False, result=result)

    _write(
        domain_dir / "contracts" / "floe" / f"{spec.product}.yml",
        render_floe_contract(spec),
        force=force,
        result=result,
    )
    if force:
        # A forced rerun may have changed sources/marts/columns, which changes
        # the Floe contract just written above. Any previously generated
        # manifest at this path was compiled from the *old* contract by
        # `make floe-manifest` and is now stale. check-project-code.sh treats
        # the manifest's mere existence as readiness, and artifact deployment
        # uploads whatever is on disk, so a stale manifest would let CI pass
        # and Dagster load outdated Floe assets/run config. Remove it so
        # `make floe-manifest` is required again before validation succeeds.
        stale_manifest = domain_dir / "contracts" / "floe" / "manifests" / f"{spec.product}.manifest.json"
        if stale_manifest.exists():
            stale_manifest.unlink()
            result.removed.append(stale_manifest)
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
    if force:
        _remove_stale(
            dbt_dir / "models" / "gold",
            glob="*.sql",
            keep={f"{mart.name}.sql" for mart in spec.marts},
            result=result,
        )
    for mart in spec.marts:
        _write(
            dbt_dir / "models" / "gold" / f"{mart.name}.sql",
            mart.sql if mart.sql.endswith("\n") else mart.sql + "\n",
            force=force,
            result=result,
        )

    raw_dir = domain_dir / "examples" / "raw" / spec.product
    for src in spec.sources:
        csv_text = _render_csv([column.name for column in src.columns], src.example_rows)
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
    if force:
        _remove_stale(
            reports_dir / "datasets" / "OpenLakeForge_Trino",
            glob="*.yaml",
            keep={f"{mart.name}.yaml" for mart in spec.marts},
            result=result,
        )
        _remove_stale(
            reports_dir / "charts",
            glob="*.yaml",
            keep={
                f"{mart.chart.name.replace(' ', '_').replace('-', '_')}_1.yaml"
                for mart in spec.marts
                if mart.chart
            },
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
    if force:
        # A --force rerun with a changed product_display_name writes the
        # dashboard under a new filename but never removed the old one on its
        # own; build_report_bundle recursively includes every YAML under the
        # report directory, so the next Superset deploy would import both the
        # obsolete dashboard and the renamed one.
        _remove_stale(
            reports_dir / "dashboards",
            glob="*.yaml",
            keep={f"{dashboard_file}_1.yaml"},
            result=result,
        )
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
