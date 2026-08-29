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
adapter. **Consumers branch on `catalog_type`**, not on a provider name, and
must not assume Polaris REST/OAuth fields are present. This is the single rule
that makes the Glue implementation possible without touching every consumer,
and it is worth stating separately because it is the one most easily broken by
a change that only ever gets tested locally.

Contract shapes are documented for capabilities that have no second
implementation yet (`secrets`, `identity`, `access`, `observability`). Those
carry local-development values today; naming the contract now is what keeps
the eventual hardening work from being a rewrite.

The current Terraform roots export v2. `olf` adapts v2 into a synthetic,
single-DEV v3 view without changing v0.2 runtime exports. A native v3 contract
requires an explicit stage. Unknown versions, fields, missing/extra stages,
cross-stage bindings, duplicate physical identities (including storage bucket
names, not only physical IDs), unknown or mismatched catalog type/provider
pairs, and provider/region/topology mismatches fail closed. Trino query
connectivity is a deliberate exception to stage isolation: `shared.query` is
one service used by every enabled stage, isolated by catalog rather than by
endpoint.

`query.catalog_name` is also the Trino catalog name (v2 kept this separate as
`trino_catalog_name`, deployed today as the fixed value `iceberg`). #133/#114
must rename the provisioned Trino catalog to `lakehouse_<stage>` before a root
emits v3.

## Consequences

Adding a provider or stage-scoped capability starts by extending v3 and its
fixtures, then implementing provider adapters. #133 and #114 consume this
contract to make the platform roots stage-aware and provision physical
resources; they must not invent a competing wire shape. They must also update
`olf.contracts.load_provider_contracts` and its callers to resolve a
`DeploymentTopology` and select a stage — today that loader refuses a v3
payload outright because no caller can consume it yet.

`tools/olf/tests/test_provider_contracts.py` validates the v3 schema and
local, Azure, and AWS fixtures against the typed parser; `olf check contracts`
covers the existing Terraform v2 surface, structured HCL, and rendered
profile/Floe output. The architecture guide records the field families and
migration boundary.

## History

2026-08-28: Rewritten for provider-contract v3. The prior flat-capability
contract remains supported only through the v2 compatibility adapter while
#133 and #114 migrate Terraform output and provisioning. Merges the decisions
previously recorded as ADR 0010 (provider-contract-first cloud readiness),
0011 (the Iceberg catalog contract allowing Glue), and 0012 (contract-driven
provider-first hardening) — those ADR numbers have since been reused for
unrelated decisions.
