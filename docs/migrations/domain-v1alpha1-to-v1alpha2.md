# Domain descriptor v1alpha1 to v1alpha2 migration

`openlakeforge.io/v1alpha2` makes the information needed for typed product
discovery explicit. It is required by the descriptor inventory used for
contracts, runtime definitions, Terraform, and E2E validation.

For each `domains/<domain>/domain.yaml`:

1. Set `apiVersion: openlakeforge.io/v1alpha2`.
2. Give every `data_products` entry a unique, identifier-shaped `id` and
   `asset_prefix`.
3. Declare a non-empty `bronze` list, non-empty
   `silver_tables.tables`, and non-empty `gold_tables.tables` for every
   product.
4. Ensure the descriptor `name` exactly matches its parent directory.
5. Validate with
   `echo '{"repo_root": "."}' | uv run --project tools/olf olf inventory terraform-external`
   (run from the repository root; the command reads its query from stdin) or
   the repository gates before deployment.

The validator still accepts the former `v1alpha1` envelope so existing metadata
can be inspected during migration. Its frozen schema remains at
`docs/schema/domain.v1alpha1.schema.json`. Inventory consumers fail closed with
this guide when a legacy descriptor has not yet been upgraded.
