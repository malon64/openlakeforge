"""Aggregate Dagster definitions for the canonical lakehouse code location."""

from __future__ import annotations

from importlib import import_module
from pkgutil import iter_modules

import lakehouse_code.pipelines.dagster as dagster_pipelines
from dagster import Definitions


def _product_definitions() -> list[Definitions]:
    """Load Definitions exposed by product pipeline modules in a stable order."""
    product_modules = sorted(
        (
            module
            for module in iter_modules(dagster_pipelines.__path__, "lakehouse_code.pipelines.dagster.")
            if not module.ispkg
        ),
        key=lambda module: module.name,
    )
    return [import_module(module.name).defs for module in product_modules]


defs = Definitions.merge(*_product_definitions())