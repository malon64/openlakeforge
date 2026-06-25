from dagster import Definitions

from domains.sales.pipelines.dagster import customer_health, order_revenue


defs = Definitions.merge(
    order_revenue.defs,
    customer_health.defs,
)
