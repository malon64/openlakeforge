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

The current domains are `sales`, `supply_chain`, and `marketing`.

## Onboarding a product

Use the golden-path scaffold; adding a product must not require shared Terraform
or platform-code changes:

```bash
uv run --project tools/olf olf product scaffold --spec /tmp/my-product.spec.yaml
```

See [docs/product-onboarding.md](../docs/product-onboarding.md) for the spec
format, what the platform derives from `domain.yaml`, the required
`make floe-manifest` step, and how to remove a product.

`marketing/campaign_performance` was created entirely through this scaffold, in a
domain that did not previously exist, and is the conformance proof that the
product contract is reusable.
