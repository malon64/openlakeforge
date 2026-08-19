# ADR 0025: `olf` owns local deployment orchestration

## Status

Accepted. Supersedes ADR 0017's shell-CLI-orchestration decision for the
local (kind-based) lifecycle only; ADR 0017 remains binding for Azure and
AWS until #125 ports them. Does not change ADR 0008's static-platform /
dynamic-artifacts boundary.

## Context

ADR 0017 kept shell as the CLI orchestrator (terraform, kubectl, helm,
docker) while Python (`tools/olf`) owned cross-environment logic reached
through `olf_run`. Issue #122 reframes that split: Python should own
deployment *lifecycle* itself - sequencing, retries, typed configuration,
and diagnostics - invoking Terraform/Helm/kubectl/Docker/kind as managed
external processes rather than being invoked by shell. Issue #123 (PR #129)
built the substrate this requires: `DeploymentContext`, `ProcessRunner`,
`RetryPolicy`, and typed adapters for each external tool
(`tools/olf/olf/tooling/*.py`). This issue (#124) is the first concrete use
of that substrate: it replaces `scripts/local/{foundation,cluster,stack}/*.sh`
with a Python `DeploymentEngine`/`DeploymentProvider` pair.

## Decision

- `tools/olf/olf/deployment/engine.py` defines the provider-neutral seam:
  `DeploymentPhase`, `Toolkit` (the shared tool-adapter bundle),
  `DeploymentProvider` (protocol: `foundation_up/down`, `prepare_images`,
  `platform_up/down`, `artifacts_deploy`, `status`, `forward`), and
  `DeploymentEngine` (sequences a provider's phases in ADR 0008 order:
  foundation -> prefetch -> platform -> artifacts; destroy runs
  platform -> foundation).
- `tools/olf/olf/deployment/local/` implements `DeploymentProvider` for the
  local kind cluster: `foundation.py`, `prefetch.py`, `platform.py`,
  `artifacts.py`, `teardown.py`, `forward.py`, plus typed configuration in
  `config.py`. Provider-neutral pieces reusable by #125's AWS/Azure
  providers live one level up: `charts.py` (Helm chart caching),
  `kube_ops.py` (namespace/job reconciliation helpers), `status.py`,
  `portforward.py`.
- Terraform/Helm/kubectl/Docker/kind remain the actual infrastructure/
  package/runtime/container/cluster engines; `olf` invokes them through the
  typed adapters from #123 and never reimplements their behavior.
- The public interface is `olf deploy|destroy|status|forward --provider
  local --profile full|slim [--phase ...]`, matching issue #122's target
  interface. `--phase` gives Make's granular targets
  (`local-foundation-up`, `local-platform-up`, ...) and the combined
  `local-up`/`local-down` the same underlying `DeploymentProvider` methods -
  nothing is duplicated between the granular and combined paths.
- `Profile` (full/slim), not ad hoc `ENABLE_GOVERNANCE`/`ENABLE_ANALYTICS`
  shell branching, drives Full-vs-Slim behavior through
  `DeploymentFeatures` and `LocalDeploymentConfig`.
- Make targets under `local-*` become one-line delegates to `olf`; no
  Terraform/Docker/kubectl/Helm invocation remains in the Makefile for the
  local lifecycle.

## Consequences

- `scripts/local/foundation/{up,down}.sh`,
  `scripts/local/cluster/prefetch-images.sh`, and
  `scripts/local/stack/{platform-up,deploy-artifacts,teardown}.sh` are
  deleted; `scripts/local/images/*.sh` remain for the standalone image
  build/load Make targets.
- Local deployment lifecycle logic is unit-tested under `tools/olf/tests/`
  (argv-exactness against `RecordingRunner`, no real Terraform/Docker/K8s
  required) instead of only being exercised end-to-end.
- AWS/Azure remain on the ADR 0017 shell-orchestration path until #125 ports
  them onto the same `DeploymentEngine`/`DeploymentProvider` seam.
- `scripts/lib/{common,helm,kube,docker}.sh` and
  `scripts/contracts/load-runtime-env.sh` stay in place - AWS/Azure and
  `scripts/artifacts/olf.sh` still use them; their removal is tracked
  alongside #125/#126.
