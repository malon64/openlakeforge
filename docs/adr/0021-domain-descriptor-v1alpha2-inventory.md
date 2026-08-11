# 0021: version the inventory-required domain descriptor shape

## Status

Accepted

## Context

`v1alpha1` accepted product descriptors without an asset prefix or complete
Bronze, Silver, and Gold declarations. The typed domain inventory needs those
fields to derive provider-neutral jobs, artifact paths, namespaces, and table
expectations. Making them required under the existing API version would silently
break valid descriptors.

## Decision

Keep `v1alpha1` validation for legacy descriptor inspection and introduce
`openlakeforge.io/v1alpha2` for inventory consumers. `v1alpha2` requires each
product's identifier, asset prefix, and non-empty Bronze, Silver, and Gold
declarations. The descriptor name must match its `domains/<domain>/` directory.
The migration guide documents the required update. Inventory loading fails with
that guide when it encounters a legacy descriptor.

## Consequences

Existing `v1alpha1` descriptors remain parseable, but must migrate before a
deployment uses typed inventory consumers. New descriptors use `v1alpha2` and
the current schema at `docs/schema/domain.schema.json`.
