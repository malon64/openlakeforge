# Domains

`domains/` contains business and data-product logic.

Each domain owns domain metadata and one or more data products. Each data
product has side-by-side assets under domain capability folders: raw examples,
dlt loaders, Floe contracts, dbt projects, Dagster modules, Superset reports,
tests, and documentation.

`domains/<domain>/domain.yaml` is a versioned (`openlakeforge.io/v1alpha1`,
`kind: Domain`) single source for domain, data-product,
Bronze, Silver, Gold, and OpenMetadata table metadata.

Keep this metadata provider-neutral: catalog/database/schema identities are
derived from the environment provider contract. Validate descriptors against
`docs/schema/domain.schema.json` before deployment.

The current seed domains are `sales` and `supply_chain`.
