"""Deterministic text splicing for `lakehouse_code/lakehouse.yaml`.

The file mixes block and flow YAML style
(`- {name: orders, source: crm, resource: orders}`), so a parse/dump
round-trip through PyYAML would rewrite the whole file and produce a hostile
diff. Instead this module inserts pre-rendered, correctly-indented text at
known anchors and leaves every other byte untouched. Correctness is not
assumed from the splice: the caller always re-validates the spliced result
through the canonical loader before committing it (see `_commit.py`).

Field order within a domain entry (`name, displayName, description, status,
silver_tables, products`) and within the document
(`apiVersion, kind, name, displayName, description, status, sources, domains,
dashboards`) is assumed to match every domain this module writes or edits --
true for every scaffold-generated domain and for the two checked-in sample
domains this module was built against.
"""

from __future__ import annotations

import re

import yaml

from olf.scaffold._shared import ScaffoldError

_TOP_LEVEL_KEY = re.compile(r"^[A-Za-z]")
_DOMAIN_START = re.compile(r"^  - name: (\S+)\s*$")
_PRODUCTS_KEY = re.compile(r"^    products:(\s*\[.*\])?\s*$")
_SILVER_TABLES_TABLES_KEY = re.compile(r"^      tables:(\s*\[.*\])?\s*$")


def _lines(text: str) -> list[str]:
    return text.splitlines(keepends=True)


def _join(lines: list[str]) -> str:
    return "".join(lines)


def _top_level_span(lines: list[str], key: str) -> tuple[int, int]:
    """Return (start, end) line indices for a top-level `key:` block."""
    pattern = re.compile(rf"^{re.escape(key)}:")
    start = next((i for i, line in enumerate(lines) if pattern.match(line)), None)
    if start is None:
        raise ScaffoldError(f"lakehouse.yaml: missing top-level key {key!r}")
    end = start + 1
    while end < len(lines) and not _TOP_LEVEL_KEY.match(lines[end]):
        end += 1
    return start, end


def _domain_span(lines: list[str], domains_start: int, domains_end: int, domain_name: str) -> tuple[int, int] | None:
    """Return (start, end) line indices for one domain entry, or None."""
    starts = [
        (i, match.group(1))
        for i in range(domains_start + 1, domains_end)
        for match in [_DOMAIN_START.match(lines[i])]
        if match
    ]
    for index, (start, name) in enumerate(starts):
        if name != domain_name:
            continue
        end = starts[index + 1][0] if index + 1 < len(starts) else domains_end
        return start, end
    return None


_FLOW_LIST_KEY = re.compile(r"^(\s*)(\w[\w-]*):\s*\[.*\]\s*$")


def _render_flow_item_as_block(item: object, *, indent: str) -> list[str]:
    """Render one already-parsed flow-list item (a scalar or a mapping, e.g.
    a dashboard `{name: ..., products: [...]}`) as block-style lines at
    `indent`. Re-parses through PyYAML rather than splitting the flow text on
    commas, since a naive split breaks on commas inside a nested
    mapping/list (`[{name: x, products: [a, b]}]`)."""
    if isinstance(item, str):
        return [f"{indent}- {item}\n"]
    dumped_lines = yaml.safe_dump(item, default_flow_style=False, sort_keys=False).splitlines()
    return [f"{indent}- {dumped_lines[0]}\n"] + [f"{indent}  {extra}\n" for extra in dumped_lines[1:]]


def _ensure_block_style(lines: list[str], start: int, end: int, *, indent: str = "  ") -> int:
    """If the list at `lines[start]` is flow style (`key: [a, b]`, including
    the empty `key: []`), rewrite `lines[start:end]` in place as block style
    with items indented by `indent`. Returns the (possibly updated) end
    index. A no-op when it is already block style."""
    stripped = lines[start].rstrip("\n")
    match = _FLOW_LIST_KEY.match(stripped)
    if match is None:
        return end
    leading, key = match.group(1), match.group(2)
    items = yaml.safe_load(stripped)[key] or []
    block_lines = [f"{leading}{key}:\n"]
    for item in items:
        block_lines.extend(_render_flow_item_as_block(item, indent=indent))
    lines[start:end] = block_lines
    return start + len(block_lines)


def add_source(text: str, source_name: str) -> str:
    """Append `source_name` to the `sources:` list, converting a flow-style
    list (`sources: [crm, erp]`) to block style first if needed."""
    lines = _lines(text)
    start, end = _top_level_span(lines, "sources")
    end = _ensure_block_style(lines, start, end)
    lines.insert(end, f"  - {source_name}\n")
    return _join(lines)


def add_domain(text: str, domain_block: str) -> str:
    """Append a fully-rendered domain entry to the end of `domains:`."""
    lines = _lines(text)
    _, end = _top_level_span(lines, "domains")
    lines[end:end] = _lines(domain_block)
    return _join(lines)


def domain_exists(text: str, domain_name: str) -> bool:
    lines = _lines(text)
    domains_start, domains_end = _top_level_span(lines, "domains")
    return _domain_span(lines, domains_start, domains_end, domain_name) is not None


def add_silver_tables(text: str, domain_name: str, table_lines: str) -> str:
    """Append pre-rendered `- {name: ..., ...}` lines to a domain's
    `silver_tables.tables:` list, converting a flow-style `tables:
    [{name: ..., ...}]` to block style first if needed. `silver_tables`
    must immediately precede `products` in the domain entry (see module
    docstring)."""
    lines = _lines(text)
    domains_start, domains_end = _top_level_span(lines, "domains")
    span = _domain_span(lines, domains_start, domains_end, domain_name)
    if span is None:
        raise ScaffoldError(f"lakehouse.yaml: domain {domain_name!r} not found")
    domain_start, domain_end = span
    tables_index = next(
        (i for i in range(domain_start, domain_end) if _SILVER_TABLES_TABLES_KEY.match(lines[i])),
        None,
    )
    if tables_index is None:
        raise ScaffoldError(f"lakehouse.yaml: domain {domain_name!r}: could not locate 'silver_tables.tables:' field")
    was_flow = _FLOW_LIST_KEY.match(lines[tables_index].rstrip("\n")) is not None
    new_tables_end = _ensure_block_style(lines, tables_index, tables_index + 1, indent="        ")
    if was_flow:
        lines[new_tables_end:new_tables_end] = _lines(table_lines)
    else:
        products_index = next(
            (i for i in range(new_tables_end, domain_end) if _PRODUCTS_KEY.match(lines[i])),
            None,
        )
        if products_index is None:
            raise ScaffoldError(f"lakehouse.yaml: domain {domain_name!r}: could not locate 'products:' field")
        lines[products_index:products_index] = _lines(table_lines)
    return _join(lines)


def add_product(text: str, domain_name: str, product_block: str) -> str:
    """Append a fully-rendered product entry to a domain's `products:` list,
    converting a flow-style list (most commonly the inline-empty
    `products: []`, but also a schema-valid non-empty flow array of product
    mappings) to block style first if needed."""
    lines = _lines(text)
    domains_start, domains_end = _top_level_span(lines, "domains")
    span = _domain_span(lines, domains_start, domains_end, domain_name)
    if span is None:
        raise ScaffoldError(f"lakehouse.yaml: domain {domain_name!r} not found")
    domain_start, domain_end = span
    products_index = next(
        (i for i in range(domain_start, domain_end) if _PRODUCTS_KEY.match(lines[i])),
        None,
    )
    if products_index is None:
        raise ScaffoldError(f"lakehouse.yaml: domain {domain_name!r}: could not locate 'products:' field")
    was_flow = _FLOW_LIST_KEY.match(lines[products_index].rstrip("\n")) is not None
    new_products_end = _ensure_block_style(lines, products_index, products_index + 1, indent="      ")
    # A flow list converts in place, so the new product goes right after the
    # converted items. An already-block-style list is untouched by
    # `_ensure_block_style` (a no-op), so `domain_end` is still valid and the
    # new product goes after the domain's existing block-style products.
    insert_at = new_products_end if was_flow else domain_end
    lines[insert_at:insert_at] = _lines(product_block)
    return _join(lines)


def add_dashboard(text: str, dashboard_block: str) -> str:
    """Append a fully-rendered dashboard entry to `dashboards:`, converting
    a flow-style list (most commonly the inline-empty `dashboards: []`) to
    block style first if needed. Unlike `sources:`/`domains:`,
    `dashboards:` has no schema minimum, so a fresh lakehouse.yaml
    legitimately starts out as `dashboards: []`."""
    lines = _lines(text)
    start, end = _top_level_span(lines, "dashboards")
    end = _ensure_block_style(lines, start, end)
    lines[end:end] = _lines(dashboard_block)
    return _join(lines)
