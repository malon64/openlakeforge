# ADR 0022: Terraform Reads the Domain Inventory via the External Provider

## Status

Accepted

## Context

Before this change, product identities (asset prefixes, Silver/Gold
namespaces, Floe manifest keys, job names, dashboard titles, OpenMetadata
FQNs) were restated by hand in `tools/olf/olf/contracts.py`,
`tools/olf/olf/e2e.py`, `tools/olf/olf/openmetadata.py`, and each Terraform
environment's `main.tf`/`contracts.tf`. Adding, renaming, or removing a data
product meant editing shared platform code in at least four places across two
languages — the single largest onboarding barrier for the small-team audience
this platform targets (issue #39).

`tools/olf/olf/inventory.py` (ADR 0021) already provides one typed, validated
`DomainInventory` model over every `domains/*/domain.yaml`, consumed directly
by Python callers. Terraform needed the same model without a second, drifting
implementation in HCL.

Two options were considered for getting the inventory into Terraform:

1. **`hashicorp/external` data source** invoking `olf inventory
   terraform-external`, which loads and validates the descriptors in Python
   and returns the typed inventory as JSON for `jsondecode()`.
2. **Native `fileset()` + `yamldecode()`** reading `domains/*/domain.yaml`
   directly in HCL and re-deriving physical names (`${asset_prefix}_silver`,
   manifest keys, and so on) with Terraform `for` expressions.

Option 2 avoids a runtime dependency on `uv`/`olf` during `plan`/`apply`, but
it means the derivation rules (how a namespace or manifest key is built from
a product's identity) exist twice: once in `olf.inventory.Product` and once
restated in HCL. That is the same two-place duplication this ADR exists to
remove, just moved from "product identities" to "name-derivation rules."

## Decision

Every environment root (`infra/terraform/environments/{local,azure-poc,aws-poc}`)
declares a `data "external" "domain_inventory"` resource that runs `uv run
--project <repo>/tools/olf --locked olf inventory terraform-external` with
`repo_root` as the query, and decodes the result into `local.inventory`. All
per-product Terraform locals — `product_floe_manifest_uris`,
`catalog_product_namespaces`, and (via `var.dagster_code_location_granularity`)
`code_locations` — are built from `local.inventory.products` /
`local.inventory.domains` rather than hand-written maps.

`olf.inventory.DomainInventory` is the single source of both the product
identities and the rules that derive physical names from them; Terraform only
consumes its `terraform_payload()` output.

## Consequences

- `terraform plan`/`apply` for any environment root requires `uv` on `PATH`.
  Every existing entry point already shells out to `uv` (the `make
  <env>-up`/`-platform-up` targets, `scripts/contracts/load-runtime-env.sh`),
  so this does not introduce a new tool dependency for operators — only a new
  point where Terraform itself invokes it.
- `terraform validate` never executes data sources, so `make check-infra` is
  unaffected. `make check-contracts` instead asserts each environment root
  declares the `external` data source and derives its namespaces from
  `local.inventory.products` (see `scripts/test/check-contracts.sh`).
- Renaming, adding, or removing a product in a `domain.yaml` changes the
  Terraform-managed namespaces, manifest URIs, and code locations with no
  Terraform or Python edit — satisfying issue #39's acceptance criteria.
- Terraform's external-provider protocol is string-only; the JSON round-trip
  through `jsondecode()` is the cost of reusing one typed model instead of
  maintaining two.
