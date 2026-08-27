# ADR 0003: Provider contracts are the portability boundary

## Status

Binding.

## Context

The platform claims that local, AWS, and Azure run the same lakehouse. That
claim only holds if components talk to *capabilities* rather than to specific
infrastructure. Otherwise replacing Polaris with Glue means editing Trino,
Dagster, dbt, OpenMetadata, and the docs — and the "same platform" is three
platforms that happen to share a repository.

## Decision

### Typed Terraform contracts are the source of truth

Each environment root (`infra/terraform/environments/<env>/contracts.tf`)
normalizes a typed object per capability and exports them as one
`provider_contracts` output. The capability set is the same in every
environment:

```text
foundation            kubernetes_platform   storage        metadata_database
catalog               query                 orchestration  reporting
governance            artifact_registry     artifact_bucket
secrets               identity              access         observability
```

Every contract carries an `implementation` and an `adapter` field naming what
satisfies it. Local and AWS differ only in those values:

| Capability | Local | AWS |
| --- | --- | --- |
| `foundation` | `foundation.kind` | `foundation.eks` |
| `storage` | `storage.s3_compatible.seaweedfs` | `storage.aws_s3` |
| `metadata_database` | `metadata_database.postgresql.in_cluster` | `metadata_database.aws_rds_postgresql` |
| `catalog` | `catalog.iceberg_rest.polaris` | `catalog.aws_glue` |
| `artifact_registry` | `artifacts.local_kind_image_load` | `artifacts.aws_ecr` |
| `identity` | `identity.local_development_credentials` | `identity.aws_pod_identity` |

### Components consume contracts, never providers

Adding a capability means extending the contract first, then writing an adapter
for each provider. A component that reads a SeaweedFS endpoint, a Polaris URL,
or the active kubectl context directly is a bug, even when it works.

Runtime configuration for Floe, dbt, Superset import, and artifact upload is
resolved from contracts. Product-owned runtime files refer to logical names such
as `lakehouse_storage` and `iceberg_catalog`; the provider adapter resolves those
to the concrete service.

### The catalog contract describes Iceberg, not Polaris

The contract carries generic catalog metadata — `catalog_type` (`rest` or
`glue`), `catalog_provider` (`polaris` or `aws-glue`), `catalog_name`, and a
`runtime_profile` selecting the runtime adapter — plus provider-specific fields
alongside them.

**Consumers branch on `catalog_type`.** They must not assume Polaris REST and
OAuth fields are present. This is the single rule that makes the Glue
implementation possible without touching every consumer, and it is worth stating
separately because it is the one most easily broken by a change that only ever
gets tested locally.

### Descriptors stay provider-neutral

`lakehouse_code/lakehouse.yaml` and `lakehouse_code/bronze/<source>/source.yaml`
must not name Polaris, Glue, S3, or SeaweedFS. Physical catalog and object-store
names are *derived* from logical identity by the provider contracts (ADR 0004),
never written into the descriptor. The JSON Schemas reject provider fields
outright, so this is enforced rather than conventional.

## Consequences

A new provider is a new set of adapters satisfying the existing contracts, not a
new deployment path. `olf check contracts` validates contract compatibility for
every environment on each pull request.

Contract shapes are documented for capabilities that have no second
implementation yet (`secrets`, `identity`, `access`, `observability`). Those
carry local-development values today; naming the contract now is what keeps the
eventual hardening work from being a rewrite.

## History

Merges the decisions previously recorded as ADR 0010 (provider-contract-first
cloud readiness), 0011 (the Iceberg catalog contract allowing Glue), and 0012
(contract-driven provider-first hardening).
