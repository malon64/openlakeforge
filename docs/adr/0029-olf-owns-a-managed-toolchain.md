# ADR 0029: `olf` owns a managed CLI toolchain

## Status

Accepted. Extends ADR 0028: `tools/olf` remains the only repository
orchestration implementation, and this ADR adds toolchain provisioning to
what it owns. Does not change ADR 0008's two-phase deploy boundary or the
`DeploymentProvider`/`Toolkit` seam ADR 0025/0027 established.

## Context

Issue [#80](https://github.com/malon64/openlakeforge/issues/80) scopes the
distribution model as "`olf` is the product deployment engine, and
OpenLakeForge owns the execution environment it needs." ADR 0028 completed
the orchestration half of that (no tracked shell, `olf` sequences
Terraform/Helm) but left the tool installation half open by design - its
Decision section states plainly that it "does not add a managed toolchain."
Until now, a consumer still had to install Terraform, Helm, kubectl, and
kind globally at version-compatible releases before `olf deploy` would run,
which means OpenLakeForge's implementation details were still the user's
prerequisite list.

Issue [#127](https://github.com/malon64/openlakeforge/issues/127) is the
distribution-foundation half of #80: the release owns, downloads, verifies,
and privately invokes its own CLI binaries. `tools/olf/olf/tooling/
resolver.py`'s `ExecutableResolver` seam (built for issue #123) already
anticipated this - its docstring named #127 as the reason tool adapters
resolve through an injected resolver rather than calling `shutil.which`
directly.

## Decision

- `tools/olf/olf/toolchain/` is a new package: `platform.py` detects the
  host `<os>-<arch>` (darwin/linux x amd64/arm64 only); `spec.py` builds a
  `ToolSpec` per tool from `release/component-catalog.yaml`'s new
  `components.toolchain` block plus a typed Python URL template per tool
  (upstream release layout is not catalog data); `download.py` fetches into
  a content-addressed cache and verifies the catalog's own `sha256` before
  anything is trusted; `install.py` extracts atomically (stage, `chmod`,
  `os.replace`) so a partial or unverified artifact never becomes the
  active binary; `manager.py`'s `ToolchainManager` ties this together with
  a JSON receipt recording exactly what is installed per distribution
  version and platform.
- Every managed tool lives under
  `<OLF_HOME>/toolchains/<distribution-version>/<os>-<arch>/bin/<tool>-<digest>`
  (`OLF_HOME` defaults to `~/.openlakeforge`), so multiple OpenLakeForge
  versions keep independent toolchains and nothing here ever mutates `PATH`
  or requires root. The activated filename is content-addressed by the
  tool's own digest, not a plain `<tool>` - two checkouts sharing
  `OLF_HOME` at the same `distribution.version` but different catalog pins
  can never collide on one mutable path that a later install could swap
  out from under an already-resolved caller.
- `tools/olf/olf/tooling/resolver.py` gains `ManagedExecutableResolver`,
  substituted into `Toolkit.default()` by a new `build_resolver()` factory.
  This is the only integration point - no tool adapter in `olf.tooling.*`
  changed, exactly what the #123 seam was built to allow. `OLF_TOOLCHAIN_MODE`
  selects `managed` (the default) or `host` (restores pre-#127 `PATH`
  resolution); an explicit resolver `overrides` mapping always wins over
  either mode, for tests and advanced overrides alike.
- Resolution fails closed: a managed tool that cannot be provisioned raises
  `ToolchainError` (a `DeploymentError`) rather than silently falling back
  to whatever happens to be on `PATH`. `olf doctor` reports resolution mode,
  distribution version, platform, and per-tool managed/host provenance, and
  provisions the toolchain as a side effect - the documented way to
  pre-warm a clean machine before `olf deploy`.
- `olf toolchain list|install|path|clean` is the diagnostic/maintenance
  surface. `clean` resolves every target against `OLF_HOME` and refuses
  anything outside it, so it cannot remove a host-installed tool.
- `olf release check` gained a pin-drift gate (`_check_toolchain_pinned`):
  every managed tool needs a concrete version (never `latest`) and a
  well-formed digest for every supported platform. The compatibility matrix
  gained a "Managed toolchain" table rendered from the same catalog block.
- CI (`checks.yml`, `local-full-e2e.yml`) replaced `hashicorp/setup-terraform`,
  `azure/setup-helm`, and `helm/kind-action` with `olf toolchain install`
  behind an `actions/cache` step keyed on the catalog file, so a normal CI
  run proves the exact clean-machine path a consumer takes.

### Scope: which tools are managed

Only `terraform`, `helm`, `kubectl`, and `kind` are managed binaries.
Reimplementing Terraform, Helm, Docker, or Kubernetes in Python is an
explicit #80 non-goal, and no Python SDK exists for any of them - CDKTF and
Pulumi's Automation API still drive a downloaded engine binary, so "SDK
instead of binary" was never on the table for these three. `kubectl` does
have an official Python client, but it doesn't cover the long-lived
background port-forward processes `olf.deployment.portforward` depends on,
and a single ~50MB static binary is the cheapest thing here to manage
regardless - not worth that refactor's risk for this change.

`docker` stays a host capability, per #127's explicit scope (do not attempt
to install/manage the host container engine in v0.2) and per #80's
non-goals.

`aws` and `az` stay declared host prerequisites for the cloud providers,
deliberately deferred rather than in scope here. Both have real Python SDKs
already available - `boto3` is already a dependency
(`tools/olf/pyproject.toml`) and used elsewhere (`olf.s3`, `olf.glue`), and
`azure-mgmt-containerservice`'s `list_cluster_user_credentials()` returns a
complete AKS kubeconfig. The reason they are not folded into this change is
`eks_update_kubeconfig`: a normal EKS kubeconfig carries an `exec:` block
that shells out to `aws eks get-token`, which would reintroduce the CLI at
kubectl runtime unless replaced with a Python-minted presigned STS token
(and its ~15-minute expiry managed explicitly). That is an authentication
semantics change, not an installation mechanics change, affects only the
POC-stage cloud providers, and deserves its own ADR - tracked as a follow-up
issue under #80 rather than bundled here.

## Consequences

- A clean machine with Docker, Python, and `olf` runs `olf doctor` then
  `olf deploy --provider local --profile slim` with no globally installed
  Terraform, Helm, kubectl, or kind, at exactly the versions
  `release/component-catalog.yaml` declares.
- Every `olf` code path that previously built a bare `["kubectl", ...]` or
  `["terraform", ...]` argv (predating the #123 tooling package: `olf.k8s`,
  `olf.contracts`, `olf.e2e._shell`, `olf.e2e._runner`) now resolves through
  the same seam, so CI dropping its setup actions doesn't silently break
  Phase 2 artifact deployment or e2e assertions.
- `aws`/`az` remain on the host prerequisite list for AWS/Azure until the
  follow-up SDK-replacement issue lands; #127's "local prerequisite list
  reduces to `olf` + a container engine" acceptance criterion is met, its
  cloud-CLI criterion is answered but deliberately not yet executed.
- Packaging `olf` itself without a host Python runtime, and the immutable
  no-clone platform payload, remain
  [#128](https://github.com/malon64/openlakeforge/issues/128)'s scope - this
  ADR provisions the toolchain a distribution needs, not the distribution
  mechanism itself.
