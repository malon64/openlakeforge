# ADR 0008: Two-Phase Deploy — Static Infrastructure and Dynamic Artifacts

## Status

Accepted

## Context

As the platform grew through iterations 3–6, the local bring-up script accumulated
responsibilities from two distinct categories: Terraform-managed service provisioning
and domain artifact deployment (manifests, images, report bundles, metadata seeds).
Running both in a single script created friction:

- Any change to domain code required reasoning about whether it was safe to re-run
  Terraform.
- CI/CD for domain changes should not touch infrastructure; collapsing both into one
  target made that boundary unclear.
- Some artifacts (the Floe manifest, Superset dashboards, OpenMetadata entities) are
  source-controlled YAML but are dynamic at deploy time — they are not Terraform
  resources and should not be modelled as such.

## Decision

The local bring-up process is split into two explicit phases:

**Phase 1 — Static platform (`make local-platform-up` / `platform-up.sh`)**

Terraform owns all infrastructure that has a platform lifetime:

- Kubernetes namespace and RBAC
- SeaweedFS, Polaris, Trino, Dagster, OpenMetadata, Superset, PostgreSQL, Redis Helm
  releases and their Kubernetes Secrets
- Persistent volumes and service-to-service credentials
- The Superset platform image build and kind load (the image changes with Superset
  version, not with domain code)

Phase 1 is idempotent via Terraform. Re-running it applies only what has drifted.
It is safe to run once after cluster creation and leave until a platform change is
needed.

**Phase 2 — Dynamic artifacts (`make local-artifacts-deploy` / `deploy-artifacts.sh`)**

A shell deploy step owns all artifacts that have a domain code lifetime:

- Build and kind-load the `project-code` image (contains domain code, dlt, dbt, Dagster
  definitions)
- Generate product Floe manifests and publish them to the SeaweedFS ops bucket
- Import product Superset report bundles via the
  Superset API
- Seed OpenMetadata with domain and data-product entities
- Rolling-restart Dagster deployments so they pick up the new `project-code` image
  under `imagePullPolicy: Never`

Phase 2 maps directly to the CD pipeline. Every domain code commit triggers Phase 2
only. Terraform is not invoked by CI for domain changes.

`make local-up` runs the foundation, local image prefetch, Phase 1, and Phase 2
in sequence for a full local bring-up from scratch.

## Consequences

The CD boundary is explicit: only `deploy-artifacts.sh` runs in CI for domain commits.
Terraform runs are a deliberate platform-level action, not an automatic CI side-effect.

Floe manifests are generated from product contracts and are always deployed from
the repo. They are build outputs, not hand-authored artifacts.

Superset dashboards and OpenMetadata entities follow a different ownership model.
The intended production workflow is UI-first: authors work freely in the Superset and
OpenMetadata UIs, and export stable artifacts to source control when they are ready to
be versioned. The repo is the export destination, not the authoring source.

The v1 POC seeds both Superset and OpenMetadata from source-controlled YAML during
Phase 2 to make the local demo stack reproducible after a teardown without manual UI
setup. This seeding direction is a POC convenience, not a production pattern. In a
production deployment, Phase 2 would apply only structural seeds (database connections,
schema registrations) and leave report and data-product authoring to UI owners.

The Dagster rolling restart in Phase 2 is the correct local substitute for a registry
push + image pull. Because `imagePullPolicy: Never` is used for local kind clusters,
Kubernetes does not pull a new image on pod creation; an explicit rollout restart is
required after `kind load docker-image` to ensure running pods use the newly built
image.

The `restart_if_exists` helper in `deploy-artifacts.sh` makes Phase 2 safe to run
against a partial stack (e.g. after a failed Phase 1 or a partial teardown).

The Superset image is built and loaded in Phase 1, not Phase 2. It is a platform image
whose version tracks the Superset Helm chart version, not domain code commits. If a
project-code image is the only change, Phase 2 alone is sufficient and the Superset
image is not rebuilt.
