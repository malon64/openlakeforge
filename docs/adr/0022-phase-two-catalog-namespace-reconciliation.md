# ADR 0022: Phase 2 Reconciles Catalog Namespaces

## Status

Accepted

## Context

ADR 0008 splits deployment in two: `make <env>-platform-up` provisions static
platform infrastructure, and `make <env>-artifacts-deploy` deploys everything
built from domain code. Product and domain definitions live in
`domains/*/domain.yaml` and are expected to change often — that is the whole
point of ADR 0021's descriptor inventory.

Namespace creation sat on the wrong side of that line for both catalog
providers, though by different mechanisms:

- The Polaris module templated a `create_namespace` shell loop into its
  bootstrap Kubernetes Job from a `catalog_namespaces` input, and re-ran the
  Job whenever a hash of that list changed.
- The AWS Glue module managed one `aws_glue_catalog_database` per namespace as
  a real `for_each`-driven Terraform resource.

Each environment root restated every product's silver and gold namespace as
literal HCL besides. Three consequences followed, for both providers:

- `platform-up` could not run against a repository with no products declared,
  because the namespace list was an input to the platform apply.
- Adding a product to a descriptor and running `artifacts-deploy` alone was not
  enough to make it queryable. Its namespace did not exist, so the first Floe
  write failed.
- Rename and relocation were not represented. Polaris's loop only ever
  created, so a renamed namespace left the old one behind; Glue's declarative
  resource would recreate to relocate, which is destructive against real
  Iceberg tables.

OpenMetadata was a second consumer of the same list on both providers: Phase 1
pre-created a `databaseSchema` entity per namespace, which
`olf openmetadata deploy-metadata` then references when it seeds table stubs
ahead of the provider's own crawler. A schema FQN pointing at a namespace that
was never physically created does not make that write succeed — it moves the
failure deeper into OpenMetadata's API. The physical namespace and the schema
entity have to be created by the same phase.

## Decision

Phase 1 stands up the catalog *service*; Phase 2 reconciles what lives inside
it — for both Polaris and Glue.

`tools/olf/olf/catalog.py` holds one provider-neutral reconciliation core:
`plan_namespace_sync` diffs the descriptor-derived desired state against
whatever a backend reports it currently holds, and `apply_namespace_sync`
executes the plan against any client that implements `create_namespace`,
`update_namespace_location`, and `drop_namespace`. `olf/polaris.py` and
`olf/glue.py` are two implementations of that same three-method surface —
neither the planner nor `olf catalog sync-namespaces` needs to know which
catalog it is talking to. `olf catalog sync-namespaces` runs first in every
`deploy-artifacts.sh`, before any table is written, and dispatches on
`OPENLAKEFORGE_CATALOG_PROVIDER`.

**Polaris.** Its bootstrap Job gains a `deployer` principal — granted
`CATALOG_MANAGE_CONTENT` through the same `create_principal_secret` and
`grant_catalog_access` helpers the Trino, Floe, and OpenMetadata principals
already use — whose credentials land in a `polaris-deployer-creds` Secret. That
principal is the only product-independent thing Phase 1 needs to hand forward.
`PolarisClient` reaches Polaris through the same `kubectl port-forward`
mechanism the revision, Superset, and OpenMetadata commands already use, and
issues the same REST calls the bootstrap Job previously made with curl.
Polaris answers 409 for a namespace that still holds tables, so `--prune`
cannot silently discard data there — the error is surfaced rather than
swallowed.

**AWS Glue.** `GlueClient` uses the same ambient AWS credentials that
`terraform apply` already used to create these databases — `deploy-artifacts.sh`
runs `olf` from the operator's shell, no new IAM grant is needed. Unlike
Polaris, `DeleteDatabase` has no built-in refusal for a non-empty database, so
`GlueClient.drop_namespace` calls `GetTables` itself first and raises before
calling `DeleteDatabase` if the database still holds tables — reproducing
Polaris's safety property rather than inheriting it for free.

Both catalog contracts stop publishing `catalog_namespaces`,
`catalog_namespace_names`, `catalog_schema_names`, `silver_namespaces`,
`gold_namespaces`, and the per-product schema FQN maps. They omit those keys
rather than emitting empty ones, because `olf/contracts.py` falls back to the
descriptor-derived values only when a key is absent. `olf openmetadata
deploy-metadata` creates each `databaseSchema` immediately before seeding that
schema's tables, replacing the Phase 1 pre-creation for both providers.

## Migration

Polaris namespaces were never Terraform-tracked resources — they were rows a
shell script inserted via a Job — so deleting the loop from the module is a
clean no-op against existing state.

Glue databases *are* Terraform-tracked resources. Deleting
`aws_glue_catalog_database.namespace` from the module without a migration step
would plan a destroy per database on the next apply, and `DeleteDatabase`
cascades to every table inside. The module instead carries a `removed` block:

```hcl
removed {
  from = aws_glue_catalog_database.namespace
  lifecycle {
    destroy = false
  }
}
```

`removed` forgets the resource from Terraform state without touching the
underlying Glue databases, so `terraform apply` after this change reports no
destroys and existing tables survive untouched. `removed` blocks require
Terraform ≥ 1.7; `aws-poc`'s `required_version` is bumped from `>= 1.6.0` to
`>= 1.7.0` to match (CI already pins 1.8.5, so no pipeline change is needed).
An operator still on Terraform 1.6 can achieve the same handover manually with
`terraform state rm 'module.glue.aws_glue_catalog_database.namespace["<name>"]'`
once per database before upgrading. The `removed` block can be deleted from the
module one release after every environment has applied past this change.

## Consequences

`platform-up` no longer reads domain code, for either provider. A repository
with zero products applies cleanly, and adding a product needs only
`artifacts-deploy`.

Namespace/database lifecycle became imperative for both providers. It is no
longer visible in `terraform plan`, and `terraform destroy` leaves namespaces
and databases behind. `--dry-run` prints the reconciliation plan as the
substitute for `plan`.

Deletion is opt-in for both. A product removed from every descriptor leaves its
namespace or database in place until someone runs `--prune`, which the deploy
scripts pass only when `OPENLAKEFORGE_CATALOG_PRUNE_NAMESPACES` is set.

`olf catalog sync-namespaces` needs cluster access for Polaris (port-forward)
and AWS credentials for Glue. It exits successfully without doing anything for
any other catalog provider.

The Glue migration has not been exercised against a real AWS account from this
change alone — `terraform plan` on `aws-poc` should be run and confirmed to
report zero destroys before this ships to an environment with existing
databases.

## References

- ADR 0008: two-phase deploy boundary
- ADR 0011 / ADR 0020: catalog contract and Polaris relational metastore, whose
  bootstrap Job hosted the original namespace loop
- ADR 0021: the descriptor inventory this command reconciles from
