from dagster import Definitions

from domains.sales.pipelines.dagster import customer_health as sales_customer_health
from domains.sales.pipelines.dagster import order_revenue as sales_order_revenue
from domains.supply_chain.pipelines.dagster import inventory_reliability as supply_chain_inventory_reliability


defs = Definitions.merge(
    sales_order_revenue.defs,
    sales_customer_health.defs,
    supply_chain_inventory_reliability.defs,
)
