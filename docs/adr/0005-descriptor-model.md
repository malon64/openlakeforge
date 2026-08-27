# ADR 0005: The v1alpha3 descriptor model

## Status

Binding.

## Context

The descriptor is the contract between a user's data products and everything the
platform derives from them: Dagster jobs, catalog namespaces, artifact paths,
Terraform inputs, and e2e expectations. Two problems shaped its current form.

First, the platform originally parsed descriptors twice — once in the `olf` CLI
and once in the project-code runtime. The two implementations had to be kept in
parity by hand while naming, validation, and identity rules were still moving.

Second, the descriptor shape itself changed when medallion ownership was
corrected (ADR 0004). The old `Domain` descriptor keyed Silver on a product's
`asset_prefix` and nested Bronze under a domain — assumptions the current
ownership model contradicts, not details that can be migrated field by field.

## Decision

### One descriptor pair, at `openlakeforge.io/v1alpha3`

| Kind | Path | Declares |
| --- | --- | --- |
| `Lakehouse` | `lakehouse_code/lakehouse.yaml` (one per project) | Sources, domains, the products each domain owns, and dashboards |
| `Source` | `lakehouse_code/bronze/<source>/source.yaml` (one per source) | A Bronze source and the resources it exposes |

`Lakehouse.domains[].silver_tables.tables[]` maps each domain table to one
`{source, resource}` pair. Products select those tables through `silver_inputs`;
dashboards select products through `products`. A product's `id` is its only
identity and is unique across the lakehouse — there is no `name`/`asset_prefix`
split.

Both are validated against JSON Schema (`docs/schema/lakehouse.schema.json`,
`docs/schema/source.schema.json`) and against the typed model. The schemas are
closed against unknown fields, which is what enforces provider-neutrality
(ADR 0003): a `bucket` or `catalog` key is rejected rather than ignored.

### `openlakeforge-domain-model` is the only implementation

The `packages/domain-model/` distribution, imported as `openlakeforge_domain`,
owns descriptor constants, loading, validation, the provider-neutral value
objects (`Source`, `Domain`, `Product`, `Table`, `Dashboard`, `LakehouseInventory`),
logical-to-physical name derivation, and artifact-path derivation. Both `olf` and
the project-code image install it.

The package has no Dagster, Terraform, or provider dependency. Dagster-specific
discovery — importing product modules and merging their `Definitions` — is a
runtime adapter in `libs/`, not part of the model.

### v1alpha1 and v1alpha2 are removed

The legacy `Domain` descriptor, its loaders and validators, and its schemas are
deleted. Nothing in the platform parses the old shape.

Upgrading a project from `v0.1.0-alpha.1` means rewriting its descriptors by
hand into the `Lakehouse` + `Source` pair. This is a real cost, accepted for two
reasons: the ownership model changed underneath the descriptor, so a mechanical
field-by-field migration would produce a syntactically valid descriptor with the
wrong Silver namespaces; and carrying a second descriptor model that no runtime
path can reach is a standing invitation for it to drift out of parity with the
one that matters — the exact failure the single-implementation decision above
exists to prevent.

Alpha releases carry no forward-compatibility guarantee, and the affected
population is small enough that a documented manual rewrite is the honest
option.

### Transitional projects

`olf init --empty` writes a `lakehouse.yaml` with empty `sources` and `domains`.
It deliberately fails strict validation: a project with no product cannot deploy
or run e2e. Only the scaffolding commands (`olf source new`, `olf domain new`,
`olf product new`) accept it, and the first `olf product new` is what turns it
into a schema-valid project.

## Consequences

A descriptor change alone moves what the platform discovers — jobs, namespaces,
artifact paths, and e2e assertions — with no edit to shared platform code. That
property is what `olf check contracts` protects on every pull request.

The inventory is cached per resolved project root, so repeated loads within one
process are free.

## History

Merges the decisions previously recorded as ADR 0021 (the inventory-required
descriptor shape), 0024 (the canonical domain-model package), 0026's descriptor
half, and 0032's transitional-project rule. ADR 0026 previously required keeping
the v1alpha1/v1alpha2 parsers for migration diagnostics; that requirement is
withdrawn here.
