# ADR 0025: Medallion Ownership and the Flat Catalog Namespace Contract

## Status

Accepted

## Context

ADR 0021 made `domains/<domain>/domain.yaml` the inventory-required descriptor and gave
every data product its own runtime identity, `asset_prefix`. Every downstream namespace
followed from it: `Product.silver_namespace` and `Product.gold_namespace` were both
`f"{asset_prefix}_{layer}"`. That made the *product* the owner of both its Silver and its
Gold namespace.

The medallion architecture this platform targets does not put one owner on all three
layers, though:

- **Bronze** is a raw, minimally transformed representation of an external source. It is
  reused across whatever downstream domains or products need it — ingesting the same CRM
  feed twice because two products want different slices of it duplicates both the
  ingestion code and the landed data.
- **Silver** is technically validated, semantically interpreted data owned by a
  business/domain boundary, not by one consumption use case.
- **Gold** is purpose-built for one data product's consumption need. Product ownership is
  correct here.

Under the v1alpha2 model, two products in the same domain — `order_revenue` and
`customer_health`, both under `sales` — got two separate Silver namespaces
(`sales_order_revenue_silver`, `sales_customer_health_silver`) even though both validate
data that logically belongs to the same `sales` domain boundary. The repository layout
made the same mistake structurally: `domains/<domain>/` nested Bronze extraction,
contracts, transformations, pipelines, and reports one level under each *domain*, with no
way to express that a Bronze source might feed more than one domain, or that a dashboard
might consume more than one product's Gold marts.

The catalog side compounds this: Polaris supports nested namespace hierarchies, but AWS
Glue supports only a single namespace/database level for the model this platform uses.
Any namespace contract that assumes nesting is not portable to Glue.

## Decision

**Repository contract.** `lakehouse_code/` becomes the canonical user-code root,
replacing `domains/` as the ownership boundary:

```text
lakehouse_code/
├── bronze/<source>/           source-aligned: source.yaml + dlt/
├── silver/<domain>/           domain-aligned: contracts/floe/ (one file per product, shared namespace)
├── gold/<product>/            product-aligned: dbt/
├── dashboards/superset/<dashboard>/   consumption-aligned
├── pipelines/dagster/         user-maintained orchestration, discovered flat (no per-domain nesting)
└── lakehouse.yaml             canonical domain/product business metadata
```

**Descriptor contract.** Two new descriptor kinds at `apiVersion: openlakeforge.io/v1alpha3`
replace the domain-scoped inventory:

- `Source` (`lakehouse_code/bronze/<source>/source.yaml`) declares the resources one
  Bronze source exposes, with no physical path.
- `Lakehouse` (`lakehouse_code/lakehouse.yaml`, one per repository) declares every domain
  and the products it owns. A product's `bronze` field is `{source, resources}` —
  a reference into a `Source` descriptor's resource list, not an inline path. A product
  no longer carries an `asset_prefix`; its `id` is its only identity, and `id` is unique
  across the whole lakehouse rather than per-domain.

The `Domain`/`v1alpha1`/`v1alpha2` envelope (ADR 0021) is kept in
`openlakeforge_domain` for migration diagnostics only — `_LegacyDomainInventory` and
`load_domain_inventory` — and is not reachable from any default runtime discovery path.

**Ownership rule**, and the reason this ADR exists:

```text
Source  -> <source>_bronze
Domain  -> <domain>_silver
Product -> <product>_gold
```

`Product.silver_namespace` now derives from `self.domain_name`, not from the product's
own identity — `f"{domain_name}_silver"` — so `order_revenue` and `customer_health`
correctly resolve to the same `sales_silver` namespace. `Product.gold_namespace` stays
`f"{id}_gold"`, product-owned as before. Both are computed once in
`packages/domain-model/openlakeforge_domain/inventory.py` and consumed everywhere else
(catalog reconciliation, Floe contract validation, dbt/Trino resolution, OpenMetadata
publication) rather than re-derived per consumer.

**Catalog namespace contract.** Namespaces stay single-level for every layer, so the
contract is identical for Polaris and Glue — no nested namespaces:

```text
<stage_catalog>.<source>_bronze.<resource>
<stage_catalog>.<domain>_silver.<table>
<stage_catalog>.<product>_gold.<table>
```

Only the stage catalog changes between environments
(`lakehouse_dev` / `lakehouse_uat` / `lakehouse_prod`). `LakehouseInventory.resolve_physical_names`
now takes a `bronze_bucket` alongside `silver_bucket`/`gold_bucket` and returns a
`PhysicalInventory` whose `catalog_namespaces` includes Bronze, Silver, and Gold
entries; `tools/olf/olf/catalog.py`'s reconciliation core (ADR 0022) needed no logic
change to pick up Bronze namespace sync — it already operated generically over
`CatalogNamespace` values.

**Sample data.** The `sales` domain demonstrates the shared-Bronze pattern for real:
`order_revenue` and `customer_health` both declare `crm.accounts` in their `bronze`
resources and both validate an `accounts` entity into `sales_silver.accounts` from
independent Floe contract files (`silver/sales/contracts/floe/{order_revenue,customer_health}.yml`).
Running either product's job re-materializes the same table idempotently; no consumer
owns a second copy of it.

## Consequences

Silver is demonstrably domain-owned, Gold is demonstrably product-owned, and Bronze is
demonstrably source-owned — this is now enforced structurally (one `bronze/<source>/`,
one `silver/<domain>/contracts/floe/` directory a domain's products all write into) and
in the derived namespace values, not just as convention.

`libs/domain_definitions.py`'s `definitions_for_domain` adapter — which ADR 0024 recorded
as the Dagster-only per-domain aggregator — has no caller left and was deleted.
`lakehouse_code/definitions.py` replaces it: a flat aggregator that scans
`lakehouse_code/pipelines/dagster/` for every product module and merges their
`Definitions`, with no domain-level grouping step. Adding a domain no longer means adding
a `definitions.py` that delegates to a per-domain adapter.

**Known limitation, not resolved by this ADR.** `libs/product_dagster.py` still keys
Bronze-loading Dagster assets as `AssetKey([product, f"{entity}_source"])` —
product-scoped, not source-scoped. Two products sharing a Bronze resource (`order_revenue`
and `customer_health` both loading `crm.accounts`) each materialize it under their own
asset key rather than one shared asset both depend on; each run just re-writes the same
Bronze data idempotently under a different key. The OpenMetadata/catalog layer does not
have this problem — `bronze_container_specs()` dedupes by physical path regardless of how
many products reference it. Making the Dagster asset graph itself represent one shared
Bronze asset needs a per-source `Definitions` group products declare `deps=` on, which is
a real design change Dagster's single-writer-per-`AssetKey` constraint makes non-trivial;
it is left as explicit follow-up work, not silently claimed as done.

Existing physical catalog namespaces created under the old product-driven Silver naming
(`<asset_prefix>_silver`) are not migrated by this ADR. An environment with data already
materialized under the old naming needs an explicit reconciliation/backfill step before
`olf catalog sync-namespaces` is run against the new contract, or it will create a second,
empty `<domain>_silver` namespace alongside the old one rather than continuing to write to
it.

`docs/schema/domain.schema.json` and `docs/schema/domain.v1alpha1.schema.json` are kept,
unreferenced by any default `contracts_check.py` path, for the same migration-diagnostic
reason `_LegacyDomainInventory` is kept.

This ADR supersedes ADR 0021's inventory-required descriptor shape for anything but
migration diagnostics; ADR 0021 remains as the historical record of why `v1alpha2` was
introduced. It does not change ADR 0022's Phase 1/Phase 2 reconciliation split or
`plan_namespace_sync`/`apply_namespace_sync` mechanics — it only adds Bronze to what
Phase 2 reconciles.

## References

- ADR 0021: the `v1alpha2` descriptor shape this ADR supersedes for non-diagnostic use.
- ADR 0022: the Phase 2 catalog namespace reconciliation core this ADR extends to Bronze.
- ADR 0024: the canonical `openlakeforge_domain` package this ADR's `Lakehouse`/`Source`
  descriptors and `PhysicalInventory` Bronze extension live in.
- [Lakehouse and Source descriptor reference](../reference/domain-descriptor.md)
- [Build your first data product](../getting-started/first-data-product.md)
