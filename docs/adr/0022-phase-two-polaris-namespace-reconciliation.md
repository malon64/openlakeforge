# ADR 0022: Phase 2 Reconciles Polaris Namespaces

## Status

Accepted

## Context

ADR 0008 splits deployment in two: `make <env>-platform-up` provisions static
platform infrastructure, and `make <env>-artifacts-deploy` deploys everything
built from domain code. Product and domain definitions live in
`domains/*/domain.yaml` and are expected to change often — that is the whole
point of ADR 0021's descriptor inventory.

Polaris namespace creation sat on the wrong side of that line. The Polaris
module templated a `create_namespace` shell loop into its bootstrap Kubernetes
Job from a `catalog_namespaces` input, and re-ran the Job whenever a hash of
that list changed. Each environment root restated every product's silver and
gold namespace as literal HCL. Three consequences followed:

- `platform-up` could not run against a repository with no products declared,
  because the namespace list was an input to the platform apply.
- Adding a product to a descriptor and running `artifacts-deploy` alone was not
  enough to make it queryable. Its namespace did not exist, so the first Floe
  write failed.
- The loop only ever created. A renamed namespace left the old one behind, and
  a relocated one kept its stale `location` property forever.

OpenMetadata was a second consumer of the same list: its Phase 1 bootstrap
pre-created a `databaseSchema` entity per namespace, which
`olf openmetadata deploy-metadata` then references when it seeds table stubs
ahead of the Polaris crawler. A schema FQN pointing at a namespace that was
never physically created does not make that write succeed — it moves the
failure deeper into OpenMetadata's API. The physical namespace and the schema
entity have to be created by the same phase.

## Decision

Phase 1 stands up the catalog *service*; Phase 2 reconciles what lives inside
it.

The Polaris module no longer takes a `catalog_namespaces` input and no longer
creates namespaces. Its bootstrap Job gains a `deployer` principal — granted
`CATALOG_MANAGE_CONTENT` through the same `create_principal_secret` and
`grant_catalog_access` helpers the Trino, Floe, and OpenMetadata principals
already use — whose credentials land in a `polaris-deployer-creds` Secret. That
principal is the only product-independent thing Phase 1 needs to hand forward.

`olf catalog sync-namespaces` runs first in `deploy-artifacts.sh`, before any
table is written. It resolves the desired namespaces from the descriptor
inventory, diffs them against the live catalog, and creates missing namespaces,
relocates namespaces whose `location` drifted, and — only under `--prune` —
drops namespaces no descriptor declares. The REST calls are the ones the
bootstrap Job previously made with curl; reconciliation gained the update and
delete cases the create-only loop never had.

The Polaris catalog contract stops publishing `catalog_namespaces`,
`catalog_namespace_names`, `catalog_schema_names`, `silver_namespaces`,
`gold_namespaces`, and the per-product schema FQN maps. It omits those keys
rather than emitting empty ones, because `olf/contracts.py` falls back to the
descriptor-derived values only when a key is absent. `olf openmetadata
deploy-metadata` creates each `databaseSchema` immediately before seeding that
schema's tables, replacing the Phase 1 pre-creation.

**AWS Glue is deliberately unchanged.** Glue databases are real Terraform
resources (`aws_glue_catalog_database`), not a templated shell loop, so moving
them is a declarative-to-imperative conversion with state-migration
consequences — existing databases would need `terraform state rm`, and
`terraform destroy` would stop removing them. The `aws-poc` root keeps its
namespace literals and its catalog contract keeps publishing them.

## Consequences

`platform-up` no longer reads domain code. A repository with zero products
applies cleanly, and adding a product needs only `artifacts-deploy`.

Namespace lifecycle became imperative for Polaris. It is no longer visible in
`terraform plan`, and `terraform destroy` leaves namespaces behind. `--dry-run`
prints the reconciliation plan as the substitute for `plan`.

Deletion is opt-in. A product removed from every descriptor leaves its
namespace in place until someone runs `--prune`, which the deploy scripts pass
only when `OPENLAKEFORGE_CATALOG_PRUNE_NAMESPACES` is set. Polaris answers 409
for a namespace that still holds tables, so a prune cannot silently discard
data; that error is surfaced rather than swallowed.

The two catalog providers now use different lifecycle models. That asymmetry is
recorded here on purpose and is the cost of not paying Glue's state-migration
bill in the same change. Aligning Glue is tracked separately.

`olf catalog sync-namespaces` needs cluster access and reaches Polaris through
`kubectl port-forward`, the same mechanism the revision, Superset, and
OpenMetadata commands already use. It exits successfully without doing anything
when the catalog provider is not Polaris.

## References

- ADR 0008: two-phase deploy boundary
- ADR 0020: Polaris relational metastore, whose bootstrap Job hosted the
  namespace loop
- ADR 0021: the descriptor inventory this command reconciles from
