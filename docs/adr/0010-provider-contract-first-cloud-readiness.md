# ADR 0010: Provider-Contract-First Cloud Readiness

## Status

Accepted

## Context

OpenLakeForge is currently implemented as a local developer stack on kind. The
project should be prepared for a later cloud provider environment, but adding
AWS resources, remote Terraform state, Keycloak, Vault, or production hardening
services now would increase the dev stack size before the provider boundary is
clear.

The existing local flow already has the right deployment shape: create the
cluster first, then apply the platform services into that cluster. A future AWS
environment should keep this split because EKS needs VPC subnets, routing, a
control plane, node groups, and cluster add-ons before pods can be scheduled.

## Decision

OpenLakeForge will refactor around provider-neutral service contracts while
keeping local as the only runnable implementation.

The local provider profile remains:

- kind for the cluster foundation;
- SeaweedFS for S3-compatible object storage;
- in-cluster PostgreSQL for platform metadata;
- Kubernetes Secrets for local secret delivery;
- local/basic application credentials;
- port-forwarded service access;
- local Terraform state.

No AWS modules, AWS provider blocks, Keycloak deployment, Vault deployment,
Secrets Manager integration, or remote Terraform state backend are added in this
iteration.

Future provider implementations must satisfy the same contracts for storage,
metadata database, catalog, image/artifact distribution, secrets, identity, and
access before platform modules consume them.

## Consequences

The local developer workflow now makes the foundation boundary explicit through
`make local-foundation-up` before `make local-up`.

Terraform modules can start receiving provider-neutral contract fields such as
implementation, auth mode, SSL mode, endpoint, and access mode without requiring
the local implementation to use cloud services.

Cloud implementation work is deferred to a later ADR. That later work should add
separate Terraform roots for cluster foundation and platform apply rather than
collapsing VPC/EKS creation and Helm platform deployment into a single tightly
coupled root.
