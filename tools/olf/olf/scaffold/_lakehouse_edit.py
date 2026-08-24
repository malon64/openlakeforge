"""Deterministic text splicing for `lakehouse_code/lakehouse.yaml`.

The file mixes block and flow YAML style
(`- {name: orders, source: crm, resource: orders}`), so a parse/dump
round-trip through PyYAML would rewrite the whole file and produce a hostile
diff. Instead this module inserts pre-rendered, correctly-indented text at
known anchors and leaves every other byte untouched. Correctness is not
assumed from the splice: the caller always re-validates the spliced result
through the canonical loader before committing it (see `_commit.py`).

Flow-style lists (`sources: [crm, erp]`, `products: [{id: x, ...}]`) are
detected and converted to block style before an item is appended, since
appending a block-style item directly after an already-closed flow value is
invalid YAML. A flow-style list is required to fit on one line (matching how
every tool and human actually writes one); a flow sequence that spans
multiple lines is not detected and is treated as unconvertible -- `_verify`
in `_commit.py` then rejects the result and nothing is written, rather than
silently producing invalid YAML. A trailing inline comment on the converted
line itself (`sources: [crm, erp]  # owners`) is dropped by the conversion;
a comment on its own line before/after the flow line is preserved.
"""

from __future__ import annotations

import re

import yaml

from olf.scaffold._shared import ScaffoldError

_TOP_LEVEL_KEY = re.compile(r"^[A-Za-z]")
_DOMAIN_START = re.compile(r"^  - name: (\S+)\s*$")
_DOMAIN_FIELD = re.compile(r"^    \S")
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


def _block_list_end(lines: list[str], list_key_index: int, domain_end: int) -> int:
    """Return the index right after a domain-level block-style list's items
    -- the first following line at the domain's own 4-space field indent (a
    sibling field, e.g. a reordered `silver_tables:` following `products:`),
    or `domain_end` if the list runs to the end of the domain entry. Makes
    `add_product`/`add_silver_tables` independent of field order within a
    domain -- neither field is required to be last, or adjacent to the
    other."""
    index = list_key_index + 1
    while index < domain_end and not _DOMAIN_FIELD.match(lines[index]):
        index += 1
    return index


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


_FLOW_LIST_KEY = re.compile(r"^(\s*)(\w[\w-]*):\s*\[.*\](\s*#.*)?\s*$")


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
    the empty `key: []`), rewrite that one line in place as block style with
    items indented by `indent`. Returns the index right after the converted
    block -- where a caller should insert a new item, immediately following
    the existing ones. A no-op (returns `end` unchanged) when it is already
    block style.

    Only `lines[start]` is replaced, never the full `lines[start:end]` span:
    `end` is "next top-level key", so anything between the flow-list line
    and `end` -- trailing comments, blank lines -- is unrelated content that
    must survive untouched, not be deleted along with the flow line.
    """
    stripped = lines[start].rstrip("\n")
    match = _FLOW_LIST_KEY.match(stripped)
    if match is None:
        return end
    leading, key = match.group(1), match.group(2)
    items = yaml.safe_load(stripped)[key] or []
    block_lines = [f"{leading}{key}:\n"]
    for item in items:
        block_lines.extend(_render_flow_item_as_block(item, indent=indent))
    lines[start : start + 1] = block_lines
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
    """Append a fully-rendered domain entry to `domains:`, converting a
    flow-style list (`domains: [{name: sales, ...}]`) to block style first
    if needed. Unlike `sources:`, `domains:` has no inline-empty form
    (schema `minItems: 1`), but a non-empty flow sequence is still valid."""
    lines = _lines(text)
    start, end = _top_level_span(lines, "domains")
    end = _ensure_block_style(lines, start, end)
    lines[end:end] = _lines(domain_block)
    return _join(lines)


def domain_exists(text: str, domain_name: str) -> bool:
    lines = _lines(text)
    domains_start, domains_end = _top_level_span(lines, "domains")
    return _domain_span(lines, domains_start, domains_end, domain_name) is not None


def add_silver_tables(text: str, domain_name: str, table_lines: str) -> str:
    """Append pre-rendered `- {name: ..., ...}` lines to a domain's
    `silver_tables.tables:` list, converting a flow-style `tables:
    [{name: ..., ...}]` to block style first if needed. Independent of
    field order within the domain (`products` need not immediately follow
    `silver_tables`, or be last)."""
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
    insert_at = new_tables_end if was_flow else _block_list_end(lines, tables_index, domain_end)
    lines[insert_at:insert_at] = _lines(table_lines)
    return _join(lines)


def add_product(text: str, domain_name: str, product_block: str) -> str:
    """Append a fully-rendered product entry to a domain's `products:` list,
    converting a flow-style list (most commonly the inline-empty
    `products: []`, but also a schema-valid non-empty flow array of product
    mappings) to block style first if needed. Independent of field order
    within the domain (`products` need not be the domain's last field)."""
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
    # `_ensure_block_style` (a no-op), so its true end must be located with
    # `_block_list_end` rather than assumed to be `domain_end` -- `products`
    # is not required to be the domain's last field.
    insert_at = new_products_end if was_flow else _block_list_end(lines, products_index, domain_end)
    lines[insert_at:insert_at] = _lines(product_block)
    return _join(lines)


def add_dashboard(text: str, dashboard_block: str) -> str:
    """Append a fully-rendered dashboard entry to `dashboards:`, converting
    a flow-style list (most commonly the inline-empty `dashboards: []`) to
    block style first if needed. Unlike `sources:`/`domains:`,
    `dashboards:` has no schema minimum, so a fresh lakehouse.yaml
    legitimately starts out as `dashboards: []`.

    `dashboards:` is always the document's last top-level key, so `end` is
    always end-of-file here (unlike every other `add_*` insertion point,
    which is always followed by more document content). If the file itself
    doesn't end in a newline, the last line must be newline-terminated
    before splicing the new dashboard in, or it concatenates directly onto
    that line instead of starting a new one.
    """
    lines = _lines(text)
    start, end = _top_level_span(lines, "dashboards")
    end = _ensure_block_style(lines, start, end)
    if end > 0 and not lines[end - 1].endswith("\n"):
        lines[end - 1] += "\n"
    lines[end:end] = _lines(dashboard_block)
    return _join(lines)
