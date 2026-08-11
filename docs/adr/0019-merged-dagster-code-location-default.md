# ADR 0019: Merged Dagster Code Location Default

## Status

Accepted

## Context

ADR 0014 changed the original aggregate Dagster code location to one location
per domain. Each location has continued to use the same immutable project-code
image and image tag; only its gRPC module argument differs. Consequently, every
domain location is rebuilt and redeployed together, so the split does not
provide independent release cadence or dependency isolation.

For the small-team target profile, each additional always-on user-code pod adds
runtime requests and operational surface without a corresponding deployment
benefit.

## Decision

Supersede ADR 0014's per-domain Dagster code-location decision. The default
configuration deploys one `openlakeforge-dagster` location loading
`domains.definitions`, which combines the existing domain definitions.

The `code_locations` Terraform configuration remains a list. Deployments that
build domains separately or need isolated code-location loading and restarts
may explicitly configure a per-domain split.

## Consequences

The default installation uses one user-code pod instead of one pod per domain.
A failed import in a merged code location prevents all domains in that location
from loading. A split configuration contains that failure and restart scope,
but costs one user-code pod per configured location.
