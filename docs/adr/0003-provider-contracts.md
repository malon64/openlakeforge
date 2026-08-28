# ADR 0003: Provider contracts are the portability boundary

## Status

Binding.

## Context

OpenLakeForge runs the same data project on local, AWS, and Azure providers.
That claim fails if a runtime reads provider details directly, or if a
multi-stage profile requires each consumer to infer physical storage and
catalog names independently.

## Decision

Terraform owns physical provider values and exports provider contracts. `olf`
owns parsing, fail-closed validation, and runtime environment generation.
Product descriptors remain provider-neutral.

Provider contract v3 has one `shared` platform family and a `stages` mapping.
The stages must equal the enabled DEV, optional UAT, and PROD stages in the
resolved `DeploymentTopology`. Shared bindings are declared once; a stage may
refer only to a shared binding or to its own stage binding.

Every stage owns distinct Bronze, Silver, and Gold storage identities, a
catalog binding, query binding, runtime identity, orchestration endpoint, and
activation prefix in shared ops storage. Reporting and governance bindings are
present exactly when their stage capability is enabled. Credential values never
appear in the contract; Secret and workload-identity references do.

Logical SQL identity is provider-neutral:

```text
lakehouse_<stage>.<owner>_<layer>.<table>
```

Catalog and object-store physical identities are derived by the provider
adapter. Consumers branch on the generic catalog type, not on a provider name.

The current Terraform roots export v2. `olf` adapts v2 into a synthetic,
single-DEV v3 view without changing v0.2 runtime exports. A native v3 contract
requires an explicit stage. Unknown versions, fields, missing/extra stages,
cross-stage references, duplicate physical identities, and topology/provider
mismatches fail closed.

## Consequences

Adding a provider or stage-scoped capability starts by extending v3 and its
fixtures, then implementing provider adapters. #133 and #114 consume this
contract to make the platform roots stage-aware and provision physical
resources; they must not invent a competing wire shape.

`olf check contracts` validates the v3 schema and local, Azure, and AWS parsed
fixtures in addition to the existing Terraform v2 surface. The architecture
guide records the field families and migration boundary.

## History

2026-08-28: Rewritten for provider-contract v3. The prior flat-capability
contract remains supported only through the v2 compatibility adapter while
#133 and #114 migrate Terraform output and provisioning.
