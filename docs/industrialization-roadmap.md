# OpenLakeForge Industrialization Roadmap

Status: Proposed

Last updated: 2026-07-16

Roadmap board: [OpenLakeForge Industrialization Roadmap](https://github.com/users/malon64/projects/3)

This document proposes the route from the current proof of concept to a
supportable OpenLakeForge distribution. It is a planning baseline, not a claim
that the capabilities below already exist. After review, the GitHub roadmap
should be reconciled with this document and remain the execution view of the
approved plan.

## Product Boundary

The first supported product is a self-hosted enterprise distribution for one
organization and one environment per deployment.

- Local kind is the developer and conformance profile.
- AWS is the first production reference profile.
- Azure remains a preview profile until after `v1.0`.
- Streaming, a SaaS control plane, hard multi-tenancy, GCP, multi-region high
  availability, and simultaneous AWS/Azure parity are outside the `v1.0`
  boundary.

OpenLakeForge is currently a late-stage proof of concept or early alpha. Its
strong foundations should be preserved: ADR-backed decisions, provider
contracts, the foundation/platform/artifact separation, shared typed deployment
tooling, and working local, Azure, and AWS paths.

## Why the Existing Roadmap Needs Rebalancing

The current roadmap is a useful component backlog, but it does not yet express
an industrialization sequence. The main changes proposed here are:

- Put security, recovery, reproducibility, and release engineering before
  production exposure rather than in a final hardening phase.
- Define install, upgrade, rollback, compatibility, and support contracts.
- Remove hardcoded product knowledge so onboarding another product does not
  require shared platform changes.
- Introduce secrets management before adding more secret-bearing identity
  clients.
- Specify provider-neutral identity and observability contracts. Keycloak and
  the OSS observability stack remain reference implementations, not mandatory
  platform contracts.
- Treat backup/restore, capacity, cost, failure recovery, SLOs, and live cloud
  validation as release gates.
- Split umbrella issues into independently testable deliverables and reconcile
  roadmap state with merged work.
- Keep lineage desirable but prevent it from blocking platform safety and
  release readiness.

## Roadmap Restructuring

The existing issues should be reorganized as follows when this proposal is
accepted:

- Keep [#17](https://github.com/malon64/openlakeforge/issues/17) and
  [#19](https://github.com/malon64/openlakeforge/issues/19) as early trust work.
- Audit [#18](https://github.com/malon64/openlakeforge/issues/18) against
  `main`, close the completed local/Azure scope, and open a separate AWS full
  end-to-end issue for any remaining work.
- Replace the separate access and identity phases with a secure-foundation
  milestone: secrets, private networking, TLS, ingress, and authentication ship
  together.
- Reframe [#24](https://github.com/malon64/openlakeforge/issues/24) through
  [#26](https://github.com/malon64/openlakeforge/issues/26) around a
  provider-neutral `identity.oidc` contract. Keycloak is an optional local or
  self-hosted adapter; AWS can accept an existing enterprise issuer.
- Keep [#21](https://github.com/malon64/openlakeforge/issues/21) through
  [#23](https://github.com/malon64/openlakeforge/issues/23) as an operability
  milestone, with backend-neutral metrics and log-export contracts.
- Split [#14](https://github.com/malon64/openlakeforge/issues/14) into product
  contract, metadata reconciliation, profiling, and governance outcomes.
- Treat [#28](https://github.com/malon64/openlakeforge/issues/28) through
  [#30](https://github.com/malon64/openlakeforge/issues/30) as incremental
  product features rather than prerequisites for platform safety.
- Break [#31](https://github.com/malon64/openlakeforge/issues/31) into
  independently verifiable security, recovery, and supply-chain work. Move
  remote state, backups, image pinning, and per-workload IAM before beta.
- Defer [#8](https://github.com/malon64/openlakeforge/issues/8) and full Azure
  managed-service parity until the AWS reference profile is stable.

## Milestone 0 — Rebaseline and Govern

Goal: make repository state, roadmap state, and release intent agree.

- Publish lifecycle definitions for alpha, beta, release candidate, stable,
  deprecated, and unsupported versions.
- Define contribution, security reporting, support, ownership, and review
  policies.
- Enforce branch protection, required CI and review, vulnerability alerts, and
  dependency update automation.
- Add release, priority, risk, effort, owner, and blocking-dependency fields to
  the roadmap.
- Replace component phases with the release milestones in this document.
- Split umbrella issues and give each item measurable acceptance criteria.
- Reconcile issue and roadmap status after every merged pull request.

Exit gate: every roadmap item belongs to a named release, has independently
testable acceptance criteria, and accurately reflects the repository.

## Milestone 1 — Reproducible Product and Release Trust (`v0.1-alpha`)

Goal: create a reproducible, versioned alpha that proves the product contract.

- Complete #19 by hashing all generated Floe artifacts, stamping the
  project-code image and uploaded manifest set, and rejecting mismatched
  revisions before rollout.
- Resolve #17 with a local-and-AWS spike. Adopt `dbt-trino` for Gold only if it
  proves atomic replacement, canonical OpenMetadata identity, green full
  pipelines, and no material runtime regression. Otherwise retain
  `dbt-trino` as the Gold compute engine, with atomic replacement and recovery tests.
- Introduce an independently versioned `domain.yaml` schema with `apiVersion`
  and `kind`.
- Remove provider-specific Polaris names from domain descriptors and derive
  physical names from provider contracts.
- Discover products, jobs, expected tables, manifests, and end-to-end
  assertions from domain descriptors rather than hardcoded lists.
- Add a golden-path product scaffold and prove a fourth sample product can be
  added without changing shared platform code.
- Establish a version catalog covering OpenLakeForge, charts, Terraform
  providers, Python dependencies, runner images, and base images.
- Lock project-code dependencies; pin container bases and GitHub Actions by
  digest or commit; make release tags immutable; publish signed images with
  SBOMs and provenance.
- Publish tagged GitHub releases with checksums, changelog, migration notes,
  compatibility matrix, and the exact component manifest.

Exit gate: a tagged alpha installs from a clean checkout, reproduces the same
artifact digests, and passes the full local result.

## Milestone 2 — Secure AWS Reference Profile (`v0.5-beta`)

Goal: establish a secure, recoverable AWS deployment suitable for controlled
beta use.

- Add encrypted remote Terraform state with locking and separate foundation and
  platform states.
- Use private worker and database subnets, private RDS, encrypted and versioned
  S3 buckets, private-by-default ingress, and configurable DNS.
- Replace the shared AWS workload policy with least-privilege Pod Identity roles
  per service account.
- Deploy External Secrets Operator with AWS Secrets Manager, remove secret
  values from Terraform outputs where possible, and test rotation. Local
  development may retain Kubernetes Secrets.
- Enforce namespace RBAC, default-deny NetworkPolicies, Pod Security Standards,
  non-root workloads, resource limits, and restricted administrative access.
- Add Traefik and cert-manager behind `access.ingress`; keep port-forwarding only
  as a development fallback.
- Add a provider-neutral OIDC contract. Restrict Dagster to platform operators
  through `oauth2-proxy`, use native OIDC where supported, and separate Trino
  human OAuth from service authentication.
- Do not block AWS on Polaris external-identity-provider support because AWS uses
  Glue. Track that requirement for the self-hosted profile.
- Enable RDS backup and point-in-time recovery, S3 lifecycle/versioning,
  Terraform-state recovery, and documented metadata/search rebuild procedures.

Exit gate: a fresh AWS deployment passes full end-to-end validation without
default credentials, public worker/database endpoints, or port-forwarding,
then passes secret-rotation and backup/restore drills.

## Milestone 3 — Operability and Lifecycle (`v0.9-rc`)

Goal: prove the release can be operated, upgraded, recovered, and supported.

- Version `observability.metrics`, `observability.logs`, and alert-routing
  contracts.
- Provide Prometheus, Grafana, Loki, and Alloy as the OSS reference stack while
  allowing compatible external backends.
- Manage dashboards and alerts as code for platform health, Dagster failures
  and queues, Trino performance, product freshness, Floe rejections,
  certificate expiry, and storage growth.
- Set the reference objectives to single-region, 99.5% monthly control-plane
  availability, metadata RPO of 24 hours, and RTO of 4 hours.
- Add load and concurrency tests and publish the measured capacity and AWS cost
  envelope. Do not advertise unsupported scale.
- Automate clean install, upgrade from the previous minor release, rollback,
  teardown, component restart, worker loss, and backup/restore scenarios.
- Publish an operations handbook for diagnosis, credential rotation, scaling,
  upgrades, disaster recovery, and dependency escalation, including the Floe
  contingency.
- Add CodeQL, Gitleaks, container and IaC scanning, dependency audits, and a
  release-blocking severity policy.

Exit gate: the release candidate meets its reference objectives and survives
upgrade, rollback, recovery, and failure drills.

## Milestone 4 — Governed Stable Distribution (`v1.0`)

Goal: ship a documented, governed distribution with a stable product contract.

- Make OpenMetadata reconciliation authoritative: distinguish intended metadata
  from discovered assets, detect missing or stale entities, and prevent
  duplicate table identities.
- Add ownership, lifecycle, classification, SLA, and quality expectations to the
  domain contract and use them in OpenMetadata and dynamic end-to-end checks.
- Enable profiling and sample data only through explicit product policy; keep it
  off by default for unclassified or sensitive assets.
- Re-enable Bronze-to-Silver, Silver-to-Gold, and dashboard lineage
  independently, with canonical-identity and stale-edge tests. A failing lineage
  adapter stays disabled without blocking the stable platform release.
- Publish complete installation, onboarding, operations, security,
  compatibility, and migration documentation.

Exit gate: fresh local and AWS installations, AWS full end-to-end validation,
upgrade, rollback, restore, security, failure-recovery, and fourth-product
onboarding gates all pass.

After `v1.0`, harden Azure through the same conformance suite: ADLS Gen2,
managed PostgreSQL, workload identity, Key Vault/External Secrets Operator,
private networking, ingress/TLS, and restore validation.

## Supported Interfaces and Versioning

- Make targets remain the supported operator interface. The `olf` CLI remains
  the tested cross-environment implementation layer.
- Add explicit preflight, conformance, backup, restore, and release-check
  targets as those capabilities are delivered.
- Add `schema_version` to `provider_contracts`. Minor releases may add compatible
  fields; removal or rename requires a major release.
- Version `domain.yaml` independently and provide validation and migration tools
  for schema changes.
- Publish a compatibility matrix for OpenLakeForge, Kubernetes, Terraform,
  charts, cloud services, and supported upgrade paths.

## Verification Policy

| Cadence | Required verification |
| --- | --- |
| Pull request | Existing static/unit checks, contract-schema validation, image build, SBOM, vulnerability/IaC/secret scans, and kind smoke |
| Main/nightly | Fresh local full end-to-end run |
| Scheduled AWS | Ephemeral deployment, all product pipelines, restore drill, and teardown |
| Release | Digest-mismatch negative test, clean install, previous-version upgrade, rollback, backup/restore, security scan, failure recovery, and fourth-product onboarding |

Release gates are cumulative. A feature can be deferred, but an unmet security,
recovery, reproducibility, or lifecycle gate cannot be waived merely to meet a
target date.

## Lifecycle Definitions

| Stage | Intended use | Compatibility commitment |
| --- | --- | --- |
| Alpha | Development and product-contract validation | Breaking changes allowed with migration notes |
| Beta | Controlled AWS evaluation | Best-effort forward migration within the beta line |
| Release candidate | Operational and upgrade qualification | No planned breaking changes before the associated stable release |
| Stable | Supported production use within the published reference envelope | Compatible changes in minor releases; breaking changes only in major releases |
| Deprecated | Still functional but scheduled for removal | Removal release and migration path published |
| Unsupported | Outside the maintained version window | No fixes or compatibility guarantees |

The exact stable support window should be declared before `v1.0`; until then,
only the latest pre-release is maintained.
