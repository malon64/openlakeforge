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

The indentation scheme (2-space list markers, 4-space domain fields, 6-space
product items, 8-space table items) is fixed, matching every domain this
module writes and the two checked-in sample domains it was built against.
A different but equally valid scheme -- notably PyYAML's own zero-indent
block-sequence style (`domains:\n- name: sales`, list markers flush with
their parent key) -- is a deliberate scope boundary, not a silent gap:
`_domain_span` then can't locate the domain and `product new`/`domain new`
fail cleanly (nothing is written) rather than misplacing content into the
wrong field.
"""

from __future__ import annotations

import re

import yaml

from olf.scaffold._shared import ScaffoldError

_TOP_LEVEL_KEY = re.compile(r"^[A-Za-z]")
_DOMAIN_ITEM_START = re.compile(r"^  - \S")
_DOMAIN_NAME_FIELD = re.compile(r"^(?:  - name:|    name:)(.*)$")
_DOMAIN_FIELD = re.compile(r"^    \S")
_PRODUCTS_KEY = re.compile(r"^    products:(\s*\[.*\])?(\s*#.*)?\s*$")
_SILVER_TABLES_TABLES_KEY = re.compile(r"^      tables:(\s*\[.*\])?(\s*#.*)?\s*$")


def _lines(text: str) -> list[str]:
    return text.splitlines(keepends=True)


def _ensure_newline_before(lines: list[str], index: int) -> None:
    """Ensure `lines[index - 1]` ends with a newline before splicing new
    content in at `index`. Property order at every level of this document
    is unconstrained (JSON Schema doesn't enforce it, and this module was
    already relaxed to accept any field order within a domain), so any
    insertion point computed as "next top-level/sibling key, or end" can
    land at true end-of-file -- not just `dashboards:`, which merely does
    so the most often. A file missing its final newline would otherwise get
    the new content concatenated directly onto its last line."""
    if index > 0 and not lines[index - 1].endswith("\n"):
        lines[index - 1] += "\n"


def _join(lines: list[str]) -> str:
    return "".join(lines)


def _top_level_span(lines: list[str], key: str, *, source_label: str = "lakehouse.yaml") -> tuple[int, int]:
    """Return (start, end) line indices for a top-level `key:` block."""
    pattern = re.compile(rf"^{re.escape(key)}:")
    start = next((i for i, line in enumerate(lines) if pattern.match(line)), None)
    if start is None:
        raise ScaffoldError(f"{source_label}: missing top-level key {key!r}")
    end = start + 1
    while end < len(lines) and not _TOP_LEVEL_KEY.match(lines[end]):
        end += 1
    return start, end


def append_to_top_level_list(text: str, key: str, addition: str, *, source_label: str, indent: str = "  ") -> str:
    """Append `addition` right before the next top-level key after `key:`
    -- or at end-of-file if `key:` is the document's last top-level key --
    rather than always appending at end-of-file. A hand-edited file (e.g. a
    Floe contract with a custom key placed after `entities:`) is not
    guaranteed to keep any particular field last. Also converts `key:` from
    flow style to block style first if needed, the same as every other
    list this module appends to."""
    lines = _lines(text)
    start, end = _top_level_span(lines, key, source_label=source_label)
    end = _ensure_block_style(lines, start, end, indent=indent)
    _ensure_newline_before(lines, end)
    lines[end:end] = _lines(addition)
    return _join(lines)


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


def _domain_name_at(lines: list[str], index: int) -> str | None:
    """Parse the `name:` scalar out of `lines[index]`, or None if it isn't a
    name field line. Parses through PyYAML rather than capturing the raw
    source text, so a quoted (`name: "sales"`) or inline-commented
    (`name: sales  # primary`) value still resolves to the plain string a
    caller compares against."""
    match = _DOMAIN_NAME_FIELD.match(lines[index].rstrip("\n"))
    if match is None:
        return None
    try:
        parsed = yaml.safe_load("name:" + match.group(1))
    except yaml.YAMLError:
        return None
    return parsed.get("name") if isinstance(parsed, dict) else None


def _domain_span(lines: list[str], domains_start: int, domains_end: int, domain_name: str) -> tuple[int, int] | None:
    """Return (start, end) line indices for one domain entry, or None.

    Domain items are located by their list-item marker (`  - `), not by
    assuming `name` is the item's first field -- a schema-valid domain can
    order its fields any way. Each item's `name` is then found by scanning
    its own lines, whether `name` is the first field (`  - name: sales`) or
    a later one (`    name: sales`).
    """
    item_starts = [i for i in range(domains_start + 1, domains_end) if _DOMAIN_ITEM_START.match(lines[i])]
    for index, start in enumerate(item_starts):
        end = item_starts[index + 1] if index + 1 < len(item_starts) else domains_end
        names = (_domain_name_at(lines, i) for i in range(start, end))
        name = next((found for found in names if found is not None), None)
        if name == domain_name:
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
        # yaml.safe_dump(..., default_style=None) lets PyYAML decide
        # whether the scalar needs quoting -- e.g. a source literally named
        # "on" must round-trip as the string "on", not the plain scalar
        # `on`, which YAML 1.1 loaders (including PyYAML's SafeLoader) read
        # back as the boolean True. `splitlines()[0]` drops the `...`
        # document-end marker safe_dump appends to a bare scalar document.
        rendered_item = yaml.safe_dump(item, default_style=None).splitlines()[0]
        return [f"{indent}- {rendered_item}\n"]
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
    _ensure_newline_before(lines, end)
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
    _ensure_newline_before(lines, end)
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
    _ensure_newline_before(lines, insert_at)
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
    _ensure_newline_before(lines, insert_at)
    lines[insert_at:insert_at] = _lines(product_block)
    return _join(lines)


def add_dashboard(text: str, dashboard_block: str) -> str:
    """Append a fully-rendered dashboard entry to `dashboards:`, converting
    a flow-style list (most commonly the inline-empty `dashboards: []`) to
    block style first if needed. Unlike `sources:`/`domains:`,
    `dashboards:` has no schema minimum, so a fresh lakehouse.yaml
    legitimately starts out as `dashboards: []`.

    `dashboards:` is normally the document's last top-level key (`end` is
    then end-of-file), but property order is unconstrained at every level
    of this document, so `_ensure_newline_before` -- not a special case
    just for this function -- guards every insertion point in this module
    against a missing final newline.
    """
    lines = _lines(text)
    start, end = _top_level_span(lines, "dashboards")
    end = _ensure_block_style(lines, start, end)
    _ensure_newline_before(lines, end)
    lines[end:end] = _lines(dashboard_block)
    return _join(lines)
