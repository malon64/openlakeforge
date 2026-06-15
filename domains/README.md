# Domains

`domains/` contains business and data-product logic.

Each domain owns domain metadata and one or more data products. Each data
product has side-by-side assets under domain capability folders: raw examples,
dlt loaders, Floe contracts, dbt projects, Dagster modules, Superset reports,
tests, and documentation.

`domains/<domain>/domain.yaml` is the single source for domain, data-product,
Bronze, Silver, Gold, and OpenMetadata table metadata.

The current seed domains are `sales` and `supply_chain`.
