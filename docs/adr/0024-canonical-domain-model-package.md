# ADR 0024: Canonical domain-model package

**Status:** Accepted

## Context

The `olf` CLI and project-code runtime each parsed `domain.yaml` through a
separate implementation. They had to be kept in parity while descriptor
validation, naming, artifact paths, and inventory identity rules evolved. The
former Terraform external-provider path also made static Terraform depend on
dynamic domain code, contrary to the Phase 1/Phase 2 boundary in ADR 0008.

Dagster still needs a runtime-specific adapter: it imports product modules and
merges their `Definitions`. That behaviour must not become a dependency of the
descriptor model.

## Decision

Create the lightweight `openlakeforge-domain-model` distribution, imported as
`openlakeforge_domain`, under `packages/domain-model/`. It owns:

- descriptor constants, loading, validation, and migration errors;
- provider-neutral `Domain`, `Product`, `Table`, inventory, and physical-name
  value objects;
- logical naming and artifact-path derivation; and
- descriptor discovery, identity validation, and process-local caching.

Both `tools/olf` and the project-code image install this distribution. The
Dagster-only adapter remains in `libs/domain_definitions.py`; individual
domain `definitions.py` modules delegate to it.

The package deliberately has no Dagster, Terraform, or provider dependency.
Terraform consumes only static provider contracts in Phase 1; descriptor-driven
catalog namespaces and artifacts remain Phase 2 work. The retired private
modules (`olf.inventory`, `olf.descriptors`, and `libs.domain_inventory`) have
no compatibility aliases.

## Consequences

There is now exactly one implementation for a domain descriptor throughout the
deployment tool and runtime image. Adding a product or a future scaffold can
use this stable API without copying parsing or naming code. Consumers must add
behaviour to the canonical package when the change is provider-neutral, or to a
small runtime/provider adapter when it is not.
