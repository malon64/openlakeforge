"""Dagster adapter for loading a domain's descriptor-selected products."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Any

from openlakeforge_domain import load_domain_inventory


def definitions_for_domain(domain_name: str, definitions_file: str) -> Any:
    """Build Dagster definitions from the canonical inventory for one domain."""
    inventory = load_domain_inventory(Path(definitions_file).parents[1])
    product_definitions = [
        import_module(product.definitions_module).defs
        for product in inventory.products
        if product.domain_name == domain_name
    ]
    if not product_definitions:
        raise ValueError(f"No products declared for domain {domain_name!r}")
    from dagster import Definitions

    return Definitions.merge(*product_definitions)
