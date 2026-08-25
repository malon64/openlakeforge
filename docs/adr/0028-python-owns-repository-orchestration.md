# ADR 0028: Python owns repository orchestration

## Status

Accepted.

## Context

ADR 0017 split orchestration between shell and Python. Subsequent ADRs moved
the local and cloud deployment lifecycles into `olf`, but script-backed checks,
artifact helpers, release helpers, and CI diagnostics remained. That left two
runtime paths and meant a distribution could still depend on checkout-local
shell files.

## Decision

`tools/olf` is the only repository orchestration implementation. It owns
deployment sequencing, artifact preparation, validation, diagnostics, and
release helper execution. Make is deprecated checkout compatibility only, and
each retained recipe delegates to one `olf` invocation. Workflow `run` steps
likewise invoke `olf` directly or use a pinned GitHub action.

Terraform remains responsible for infrastructure state, plans, applies,
destroys, and drift. Helm remains responsible for chart preparation, rendering,
and releases. `olf` invokes these engines using structured argument vectors,
with typed provider/profile/phase configuration, retry policy, diagnostics, and
contract hydration. It sequences foundation, Terraform-managed static
platform, and dynamic artifacts without changing ADR 0008's two-phase boundary.

No tracked shell orchestration is permitted. A future shell orchestration path
requires a new ADR. This decision does not add a managed toolchain, immutable
payload installation, or a standalone binary; those remain distribution work.

## Consequences

- Users run `olf doctor`, `olf plan`, and lifecycle commands directly.
- `olf` commands resolve contract outputs internally; callers never source
  exported shell state.
- The structural gate rejects tracked `.sh` files.
- Historical ADR text is retained unchanged. This ADR fully supersedes ADR
  0017's shell-orchestration decision.
