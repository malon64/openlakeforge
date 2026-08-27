# ADR 0008: `olf` owns orchestration and its managed toolchain

## Status

Binding.

## Context

Deployment automation was once ~6,900 lines of shell across `scripts/local`,
`scripts/azure`, and `scripts/aws`. Environment-agnostic logic was duplicated
three times, and several concerns lived as Python heredocs inside bash —
contract resolution, OpenMetadata REST seeding, Superset bundle handling,
object-storage uploads, and Kubernetes image bookkeeping.

A first correction split responsibilities: shell stayed the CLI orchestrator
(invoking terraform, kubectl, helm, docker) while Python owned cross-environment
logic. That left two runtime paths and meant a distribution could still depend on
checkout-local shell files.

A second problem sat alongside it. Even with orchestration solved, a user had to
install Terraform, Helm, kubectl, and kind globally at compatible versions before
`olf deploy` would run — OpenLakeForge's implementation details were the user's
prerequisites.

## Decision

### `olf` is the only orchestration implementation

`tools/olf` owns deployment sequencing, artifact preparation, validation,
diagnostics, and release helpers. It invokes Terraform, Helm, kubectl, Docker,
and kind as managed external processes with structured argv, retries, and
diagnostics.

Terraform remains the state and drift engine; Helm remains the chart and release
engine. `olf` does not reimplement either — it sequences them.

There is **no tracked shell**. `olf check structure` rejects shell scripts in the
repository, so this is enforced rather than conventional. `Makefile` targets are
deprecated checkout compatibility: each is a one-line delegate to the equivalent
`olf` command, and none is the supported interface.

The substrate is a `DeploymentEngine` sequencing a `DeploymentProvider`, with
typed adapters per external tool under `tools/olf/olf/tooling/`. Cloud providers
share one `CloudProvider`; a `CloudBackend` protocol isolates the genuine AWS and
Azure differences (foundation variables, kubeconfig population, registry login,
default image repository, Floe profile selection) rather than forking the
lifecycle.

### `olf` provisions its own toolchain

`olf` downloads, checksum-verifies, and privately invokes its own Terraform,
Helm, kubectl, and kind under `OLF_HOME` (default `~/.openlakeforge`), at the
exact versions `release/component-catalog.yaml` pins.

This follows from the pins being immutable at all: a catalog that pins Terraform
1.8.5 while the user's `PATH` supplies 1.5 is a pin in name only.

`OLF_TOOLCHAIN_MODE=host` resolves from `PATH` instead, for users who need to
supply their own binaries.

### Cloud authentication goes through SDKs

AWS IAM Identity Center and Microsoft Entra authentication run through boto3 and
the Azure SDKs. Neither the `aws` nor the `az` CLI is a prerequisite —
`olf auth login --provider aws|azure` opens the vendor-hosted sign-in page and
OpenLakeForge never collects or hosts credentials itself.

Two provider constraints survive as narrowly-scoped credential bridges, because
the alternative is worse:

- EKS kubeconfigs normally use an `exec` plugin that invokes `aws` for every
  token. `olf` supplies its own credential process instead of requiring the CLI.
- The pinned AzureRM provider asks an executable named `az` for account metadata
  and tokens under interactive user auth. A minimal bridge satisfies that
  without a full CLI install.

These are documented deliberately: they are the seams where the "no vendor CLI"
claim is doing real work, and any change to them is a change to that claim.

## Consequences

A clean machine needs Docker and Python 3.12. Everything else `olf` provides.

Adding a deployment step means adding it to a provider implementation with
tests, not to a script. New `olf` behaviour ships with tests in the same change.

The managed toolchain is cached in CI by `release/component-catalog.yaml` hash,
so pinning a new version invalidates exactly the right cache entry.

## History

Merges the decisions previously recorded as ADR 0028 (Python owns repository
orchestration, which fully superseded ADR 0017's shell/Python split after ADRs
0025 and 0027 ported the local and cloud lifecycles), 0029 (the managed
toolchain), and 0030 (SDK-managed cloud authentication).
