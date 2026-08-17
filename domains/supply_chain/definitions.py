"""Delegate supply-chain Dagster definition discovery to the shared adapter."""

from __future__ import annotations

from libs.domain_definitions import definitions_for_domain

defs = definitions_for_domain("supply_chain", __file__)
