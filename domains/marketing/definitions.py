from dagster import Definitions

from domains.marketing.pipelines.dagster import campaign_performance


defs = Definitions.merge(
    campaign_performance.defs,
)
