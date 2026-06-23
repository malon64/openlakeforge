from dagster import Definitions

from domains.supply_chain.pipelines.dagster import inventory_reliability


defs = Definitions.merge(
    inventory_reliability.defs,
)
