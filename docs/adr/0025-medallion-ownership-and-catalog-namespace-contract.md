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
├── silver/<domain>/           domain-aligned: one Floe contract and manifest
├── gold/<product>/            product-aligned: dbt/
├── dashboards/superset/<dashboard>/   consumption-aligned
├── pipelines/dagster/         user-maintained orchestration, discovered flat (no per-domain nesting)
└── lakehouse.yaml             canonical domain/product business metadata
```

**Descriptor contract.** Two new descriptor kinds at `apiVersion: openlakeforge.io/v1alpha3`
replace the domain-scoped inventory:

- `Source` (`lakehouse_code/bronze/<source>/source.yaml`) declares the resources one
  Bronze source exposes, with no physical path.
- `Lakehouse` (`lakehouse_code/lakehouse.yaml`, one per repository) declares every
  domain and the products it owns. `domains[].silver_tables.tables[]` maps each
  domain table to one `{source, resource}` pair. Products select those tables through
  `silver_inputs`; dashboards select one or more products through `products`. A product
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

`Domain.silver_namespace` derives as `f"{name}_silver"`, so `order_revenue` and
`customer_health` correctly resolve to the same `sales_silver` namespace through their
domain. `Product.gold_namespace` stays `f"{id}_gold"`, product-owned as before. The
physical names are computed once in
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

The physical contract identifies Bronze maps by source, Silver maps by domain, and Gold
maps by product. Its namespace model marker is `medallion-owner`. Floe manifests and
report prefixes are domain-owned; dbt artifact prefixes remain product-owned.

**Sample data.** The `sales` domain demonstrates the shared-Bronze pattern for real:
`order_revenue` and `customer_health` both select `sales.accounts`, which maps to
`crm.accounts`. One source-owned Dagster multi-asset exposes `[crm, accounts]`; one
domain-owned Floe definition exposes `[sales, accounts]`. Both product jobs select those
same executable definitions and physical tables, so there is no competing producer.

## Consequences

Silver is demonstrably domain-owned, Gold is demonstrably product-owned, and Bronze is
demonstrably source-owned — this is now enforced structurally (one `bronze/<source>/`,
one `silver/<domain>/contracts/floe/` directory a domain's products all write into) and
in the derived namespace values, not just as convention.

`libs/domain_definitions.py`'s `definitions_for_domain` adapter — which ADR 0024 recorded
as the Dagster-only per-domain aggregator — has no caller left and was deleted.
`lakehouse_code/definitions.py` replaces it: the root aggregator creates one subsettable
Bronze multi-asset per Source, loads one Floe manifest per Domain, scans
`lakehouse_code/pipelines/dagster/` for product jobs and dbt Gold assets, and merges the
three ownership groups. Floe source dependencies are remapped through Dagster's public
`AssetsDefinition.with_attributes(asset_key_replacements=...)` API. Repository validation
loads all definitions and resolves all jobs eagerly, matching the gRPC code server.

Existing physical catalog namespaces created under product-driven Silver naming are not
migrated. This alpha change is reset-only: namespace reconciliation aborts when it sees a
noncanonical `*_silver` namespace and tells the operator to destroy and recreate the
environment. No namespace rename, data backfill, or in-place Polaris allowlist update is
part of this change.

Bronze becoming a real Polaris namespace also means Polaris's catalog-level
`storageConfigInfo.allowedLocations` allowlist (`infra/terraform/modules/catalog/polaris/templates/bootstrap.sh.tftpl`)
had to grow a third entry for the bronze bucket, alongside the pre-existing silver/gold
entries — Polaris refuses to create a namespace whose location isn't in that list. The
bootstrap job only creates the Polaris catalog once, which is another reason existing
alpha installations must be destroyed and recreated.

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
