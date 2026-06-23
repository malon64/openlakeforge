from dagster import Definitions

from domains.sales import definitions as sales_definitions
from domains.supply_chain import definitions as supply_chain_definitions


defs = Definitions.merge(
    sales_definitions.defs,
    supply_chain_definitions.defs,
)
