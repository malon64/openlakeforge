# ADR 0027: `olf` owns AWS/Azure deployment orchestration

## Status

Accepted. Supersedes ADR 0017's shell-CLI-orchestration decision for the
AWS and Azure cloud lifecycles, completing what ADR 0025 left open for the
local (kind-based) lifecycle only. Does not change ADR 0008's
static-platform / dynamic-artifacts boundary, ADR 0011's catalog-contract
allowance for Glue, or ADR 0020's Polaris-for-local-and-Azure /
Glue-for-AWS split.

## Context

ADR 0025 ported the local kind lifecycle from shell onto `olf`'s
`DeploymentEngine`/`DeploymentProvider` seam and deliberately left
AWS/Azure on ADR 0017's shell-orchestration path, tracked as issue #125.
`scripts/aws/{foundation,stack,images}/*.sh` and
`scripts/azure/{foundation,stack,images}/*.sh` duplicated the same
foundation/platform/image/artifact orchestration shape while differing
only in provider-specific bootstrap, authentication, registry, storage,
and Kubernetes details - exactly the duplication ADR 0025's seam exists to
remove.

## Decision

- `tools/olf/olf/deployment/cloud/` implements `DeploymentProvider` for
  AWS and Azure as **one** `CloudProvider`, not two duplicated providers.
  `foundation.py`, `platform.py`, `images.py`, `artifacts.py`,
  `teardown.py`, and `forward.py` hold the lifecycle logic shared by both
  clouds - Terraform init/apply/import/destroy, namespace adoption, Trino
  and Dagster chart caching, and artifact-deployment ordering are
  byte-for-byte identical between the two shell script trees this
  replaces.
- The handful of things that actually differ between AWS and Azure -
  foundation Terraform variables, kubeconfig population (`aws eks
  update-kubeconfig` vs `az aks get-credentials`), registry login (ECR vs
  ACR), the default project-code/Superset image repository, whether Polaris
  bootstrap jobs are cleaned up before a platform apply retry, and Floe
  profile selection (AWS's per-product Glue-database profile vs the
  rendered `local-k8s.yml` profile Azure shares with local) - are isolated
  behind a `CloudBackend` protocol (`tools/olf/olf/deployment/cloud/
  backend.py`), with `AwsBackend`/`AzureBackend` as the two concrete
  implementations. Nothing provider-specific leaks into the shared engine.
- `DeploymentContext.kube_context` is unknown for a cloud context until the
  foundation's Terraform outputs are read (unlike local's static
  `kind-<cluster>`). `CloudProvider` resolves this once, lazily, through a
  `FoundationFacts` value read from the foundation's Terraform state; every
  phase after foundation depends on it, and the foundation phase itself
  never touches it.
- Three provider-neutral modules that #125 needed but #124 didn't build
  moved out of `deployment/local/` into `deployment/` directly, so both
  providers share them: `contract_env.py` (the in-process replacement for
  sourcing `scripts/contracts/load-runtime-env.sh`), `artifact_steps.py`
  (catalog sync, Floe revision activation/upload, optional-layer deploy,
  now parameterized by `--via port-forward|direct`), and
  `floe_manifests.py` (manifest generation, now carrying a
  `ProfileStrategy` seam instead of being local-only).
- `olf e2e run` resolves and applies the provider-contract environment
  itself, so the granular Make target no longer needs
  `scripts/artifacts/olf.sh` as a wrapper; `local-e2e`, `azure-e2e`, and
  `aws-e2e` all call `olf e2e run --env <provider>` directly.
- The public interface is unchanged in shape: `olf deploy|destroy|status|
  forward --provider aws|azure --profile full|slim [--phase ...]`, matching
  the local provider's interface from ADR 0025. `Profile` (not the shell's
  ad hoc `ENABLE_GOVERNANCE`/`ENABLE_ANALYTICS` variables) drives
  Full-vs-Slim behavior for cloud too, extending ADR 0025's precedent
  rather than introducing a new mechanism.
- Make targets under `azure-*`/`aws-*` become one-line delegates to `olf`,
  same as `local-*`; no Terraform/Docker/kubectl/Helm/az/aws invocation
  remains in the Makefile for either cloud lifecycle.

## Consequences

- `scripts/aws/**` and `scripts/azure/**` are deleted (14 files); no cloud
  deployment shell script remains.
- AWS/Azure deployment lifecycle logic is unit-tested under
  `tools/olf/tests/` (argv-exactness against `RecordingRunner`, no real
  Terraform/AWS/Azure CLI required) instead of only being exercised
  end-to-end - the same testing posture ADR 0025 established for local.
- `scripts/lib/{common,helm,kube,docker}.sh` and
  `scripts/contracts/load-runtime-env.sh` stay in place - `scripts/
  artifacts/floe-manifest.sh`, `dbt-parse.sh`, and the standalone artifact
  Make targets (`floe-manifest-upload`, `superset-reports-*`,
  `openmetadata-metadata-deploy`) still use them; their removal is tracked
  in #126 alongside final public-interface cleanup.
- Two provider divergences are preserved exactly as observed rather than
  unified: Azure's platform apply cleans up failed
  `polaris-*-bootstrap-*` jobs before each retry and AWS's does not; AWS's
  platform Terraform apply/destroy optionally references a `sandbox.tfvars`
  file (used only if present) while Azure's foundation apply requires one
  and Azure's platform apply never references a tfvars file at all. These
  read as shell-script drift, not deliberate design, but unifying them is
  out of scope for a migration PR.
- Real AWS/Azure verification is constrained by lab sandbox access (see
  the AWS sandbox notes referenced from the project's operational memory);
  confidence rests on unit coverage plus a manual `make aws-up`/`make
  azure-up`/`make aws-e2e`/`make azure-e2e` pass where infrastructure is
  reachable, matching the acceptance criteria issue #125 already scoped
  as "where infrastructure is available."
