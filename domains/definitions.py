"""Aggregate Dagster definitions for the default shared code location."""

from __future__ import annotations

from importlib import import_module
from pkgutil import iter_modules

import domains
from dagster import Definitions


def _domain_definitions() -> list[Definitions]:
    """Load Definitions exposed by direct domain packages in a stable order."""
    domain_packages = sorted(
        (module for module in iter_modules(domains.__path__, "domains.") if module.ispkg),
        key=lambda module: module.name,
    )
    return [import_module(f"{module.name}.definitions").defs for module in domain_packages]


defs = Definitions.merge(*_domain_definitions())
