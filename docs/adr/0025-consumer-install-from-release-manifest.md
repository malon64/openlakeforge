# ADR 0025: Consumer Install from the Release Manifest

## Status

Accepted

## Context

`v0.1.0-alpha.1` publishes a changelog, checksums, a signed checksum bundle, a
compatibility matrix, a component catalog, `component-manifest.json`, and
SPDX SBOMs for both images. That is strong supply-chain hygiene, and none of
it is installable: the only supported way to run OpenLakeForge is to clone
the repository and run `make local-up`. The immutable tag and verified
digests consumers are told to pin to could not be consumed as a unit — the
release was a proof of process, not a distributable product (#80).

Two distribution mechanisms were considered:

1. **A published Helm chart** (`helm install oci://ghcr.io/.../openlakeforge
   --version <tag>`). Idiomatic for Kubernetes consumers, but the platform is
   not deployed by Helm alone today — `infra/terraform/modules/*` wraps each
   `helm_release` with provider-contract wiring, and the dynamic half of the
   deploy (catalog namespace reconciliation, Floe manifest generation, image
   rollout, optional-layer artifacts — ADR 0008, ADR 0017, ADR 0022) is
   `olf`/shell, not Helm at all. An umbrella chart would have to
   re-implement Phase 1 in Helm — a second deploy model the Terraform one
   would inevitably drift from — and would still cover none of Phase 2.
2. **An `olf install` command** consuming `component-manifest.json`. Reuses
   the exact Terraform roots, Helm values, and phase scripts `make local-up`
   already deploys from; the maintainer and consumer paths cannot diverge
   because they are the same code path with different inputs.

`(2)` was chosen.

A second constraint followed from the first: `infra/terraform/environments/local`
hard-required the kind foundation Terraform root's state
(`data.terraform_remote_state.local_foundation`), which does not exist for a
cluster `olf install` targets — an EKS/AKS/GKE cluster, or any kind cluster
not created by that root. The foundation contract needed a second source.

## Decision

**`olf install`, not a Helm chart.** `tools/olf/olf/install.py` adds `olf
install run` and `olf install verify`, following the module-per-concern
pattern of `tools/olf/olf/release.py` and `smoke.py`. `install run` fetches
(or reuses `--assets-dir`) a tagged release's assets, authenticates
`checksums.txt` with its keyless Sigstore bundle, verifies every asset
checksum and both image signatures — reusing the identity-regexp and cosign
invocation `scripts/release/verify-install.sh` already proved — then drives
`scripts/local/stack/platform-up.sh` and `deploy-artifacts.sh` with image
references and layer toggles resolved from `component-manifest.json`. Nothing
about those phase scripts changed in what they deploy; only their inputs can
now come from a downloaded manifest instead of repository state.

**Existing-cluster foundation mode, not a second Terraform root.**
`infra/terraform/environments/local` gained `foundation_mode`
(`kind-foundation-state` default | `existing-cluster`). In
`existing-cluster` mode, `data.terraform_remote_state.local_foundation` is
skipped (`count = 0`) and the foundation contract is built directly from
`cluster_name`/`kube_context`/`kubeconfig_path` variables with
`implementation = "foundation.existing_cluster"`. This is a contract
extension per the "contracts before providers" rule (AGENTS.md #1): every
other local in this file — modules, Helm values, layer toggles — is
untouched, so `make local-up` and `olf install` apply the identical
platform. The alternative (a dedicated `environments/install` root) was
rejected: it would duplicate ~280 lines of module composition that would
need hand-syncing with `environments/local` on every future change —
exactly the drift this ADR exists to avoid.

**Terraform state lives outside the bundle `olf install` re-extracts on
every run, via an explicit backend path — never inside it, with a
carve-out to protect it.** An earlier iteration left Terraform's state file
inside the extracted bundle tree and special-cased its filename in
`unpack_install_bundle`'s wipe-and-re-extract step so a repeat run or
upgrade would not delete it. That carve-out was itself the bug class: any
future durable file (the storage override below, the target-identity
marker) needed its own special case to avoid the same fate, and getting
the carve-out list wrong in either direction is silent — a state file that
starts landing somewhere the list doesn't cover gets wiped with no error.
`infra/terraform/environments/local/main.tf` now declares an explicit
`backend "local" {}` (previously implicit — the same default path either
way, so `make local-up` is unaffected), and `olf install run` passes
`terraform init -backend-config="path=<work-dir>/state/terraform.tfstate"`
— a location `unpack_install_bundle` never touches, so `bundle/` can now be
wiped with zero exceptions except Terraform's own provider plugin cache
(`.terraform/`, preserved purely for speed — unlike state, a stale cache is
self-healing, never a correctness risk). The storage override and
target-identity marker (below) moved out of `bundle/` too, into
`<work-dir>/config/`, for the same reason.

**`olf install` creates a namespace or refuses; it never adopts one
implicitly.** `platform-up.sh`'s pre-existing drift recovery
(`reset_drifted_local_platform_if_needed`) was written for the
`make local-up` dev loop: a disposable kind cluster the developer fully
owns, where finding in-cluster resources with no matching Terraform state
means a previous run of the *same tooling* left drift, safe to
auto-reset (which shells out to `teardown.sh`, deleting the namespace).
That assumption does not hold in `existing-cluster` mode — the target is a
real cluster `olf install` does not own the history of, so a namespace it
finds with no state record could be anything. `install.py`'s
`check_namespace_ownership` replaces drift-reset for that mode: if
Terraform state already owns the namespace, proceed (normal re-run/upgrade);
if the namespace doesn't exist yet, proceed (`terraform apply` creates it);
otherwise refuse with a non-zero exit unless `--adopt-namespace` was
passed. This needs no inspection of the namespace's *contents* — ownership
is settled by Terraform state alone, so there is nothing left to enumerate
or get wrong about what kinds of objects a namespace might hold.

This is Python, not a `platform-up.sh` bash function, and runs before that
script is ever invoked (right after `unpack_install_bundle`, shelling out
to `terraform init`/`state show` and `kubectl get namespace` itself) --
AGENTS.md #4 ("Python for behaviour, shell for structure", ADR 0017)
draws this exact line: cross-environment behavioral policy belongs in
`tools/olf` with tests, and shell is for filesystem/Terraform/Helm
structure only. An earlier iteration put this decision directly in
`platform-up.sh`; it moved here once review caught the boundary violation,
with unit tests exercising every branch (already-owned, doesn't-exist-yet,
adopting, refusing, and the `terraform init` failure path) by injecting a
fake subprocess runner, the same pattern `cosign_verify_blob`/
`cosign_verify_image` already use.

Adoption reopens a question Phase 2's own Superset digest-pinning had
glossed over: `discover_superset_deployments` used to match Deployments by
name prefix (`"superset"`/`"superset-*"`). In an adopted namespace, a
foreign Deployment coincidentally named e.g. `superset-exporter` would
match that prefix and have its containers silently overwritten with the
Superset image. It now selects by the chart's own ownership labels
(`release=<release-name>,heritage=Helm` — confirmed empirically against
the pinned chart version that it uses this pre-`app.kubernetes.io/*` Helm
convention, not the modern `app.kubernetes.io/instance`), so only
Deployments the Superset `helm_release` actually created are ever patched.

**Phase 1 deploys by tag; Phase 2 patches to the verified digest directly.**
`.github/workflows/release.yml` retags every pushed digest with the plain
catalog version (`docker buildx imagetools create -t "<repo>:<version>"
"<repo>@<digest>"`), so `<repo>:<version>` is guaranteed to resolve to
exactly the digest recorded in `component-manifest.json`'s `resolved_images`
*at publish time*. `olf install run`'s Phase 1 derives
`PROJECT_CODE_IMAGE_REPOSITORY`/`TAG` and the Superset equivalents from that
guarantee — the same `repository`/`tag` Terraform variables `make local-up`
already uses, unchanged. That guarantee alone is not sufficient: a version
tag is mutable, and if it were moved after publication (registry
compromise, misconfigured push access) without moving the digest, deploying
by tag would run whatever the tag currently resolves to — not the bytes
cosign verified.

Phase 2 closes that window: `olf k8s set-project-code-image` (pre-existing)
and the new `olf k8s set-superset-image` re-point every running container at
the exact `repo@sha256:...` reference from `resolved_images` via `kubectl
patch`, which accepts a digest reference natively, independent of whether
the underlying Helm chart itself supports digest pinning. This mattered
concretely for Superset: its chart hardcodes `{{ .Values.image.repository
}}:{{ .Values.image.tag }}` with no digest field at all, so chart-level
pinning was never an option for it. (The Dagster chart's own image helper
*does* support an optional `digest` field for three of its four image
references — but `kubectl patch` bypasses the question entirely for both
charts, and is the single mechanism the default install path relies on.)
Phase 2 runs by default (`--skip-artifacts` opts out — see below), so a
default install's *final*, settled state is always digest-pinned. `olf
install verify` proves this by reading back every running container's
*resolved* `imageID` (`repo@sha256:...`, which the kubelet always records
regardless of whether the pull used a tag or a digest) and comparing it
against the manifest.

This leaves a real, bounded window even on the default path: Phase 1 apply
and Phase 2 patch run sequentially within one `olf install run` invocation,
with no user action between them, but they are not atomic — for the seconds
to low minutes between Phase 1's pods coming up on the tag-resolved image
and Phase 2's `kubectl patch` landing, a pod is running whatever the tag
currently resolves to, not yet the digest `resolved_images` names. If the
tag were moved in that specific window, Phase 1 would briefly run
unverified bytes before Phase 2 immediately re-pins to the cosign-verified
digest. This is accepted as a residual gap, not eliminated: removing it
would mean deploying by digest from the first apply, which Superset's chart
cannot do at all (no digest field — see above) and Dagster's can only do
partially, so Phase 1 would still need the same `kubectl patch`-after-apply
step Phase 2 already is — replacing it with an unverifiable initial state
instead of a verified one. The window does not recur past that single
install run: once Phase 2 completes, the pinned digest is what every
subsequent restart and reconciliation runs, and `olf install verify` proves
the settled state, not the transient one.

**Install goes through Phase 2, not just Phase 1.** `olf install run`
applies the static platform and then deploys dynamic artifacts by default
(`--skip-artifacts` opts out), so a first-time consumer ends up with a
working lakehouse, not a stack of Helm releases with nothing to query. This
follows ADR 0008's Phase 1/Phase 2 boundary exactly; `olf install` is a new
input to that boundary, not a new phase. `--skip-artifacts` also means
skipping the digest re-pin described above — a Phase-1-only install stays on
whatever the version tag resolved to at apply time. This is documented as an
explicit trade-off of that flag, not a silent gap.

## Consequences

`make local-up` is unchanged in behaviour — it always ran with
`foundation_mode = kind-foundation-state` (the new variable's default) and
`image_repository`/`image_tag` variables it already had.

`olf install` deploys `infra/helm/values/local` unmodified — the same
values a throwaway kind cluster uses. SeaweedFS's data volumes (master,
volume, filer — where Bronze/Silver/Gold actually live) are `emptyDir`, not
PVC-backed; a pod restart on a real cluster loses all lakehouse data.
PostgreSQL is already PVC-backed independent of `emptyDir`: it is not
deployed from a Helm values file at all (unlike SeaweedFS) but from
`infra/terraform/modules/storage/postgresql`'s own
`kubernetes_stateful_set_v1` with a `volume_claim_template`
(`access_modes = ["ReadWriteOnce"]`) directly in the Terraform module --
deleted the stale `infra/helm/values/local/postgresql.yaml` this ADR used
to (incorrectly) cite here, left over from before that resource became a
native StatefulSet and unreferenced by any module since. There is no
built-in persistent-storage profile yet, but `olf install run
--storage-values-override <file>` layers a PVC-backed values file on top of
the bundle's SeaweedFS values from a path outside the bundle's own
extraction tree (`unpack_install_bundle` deletes and re-extracts that tree
on every run, so an override edited inside it would not survive a
re-install). The flag only needs passing once: `resolve_storage_values_override`
copies its content into `<work-dir>/config/` on first use and every later
run against the same target reuses that persisted copy automatically, even
one that omits the flag -- otherwise an upgrade that forgot to repeat it
would silently revert SeaweedFS to `emptyDir` values, which can fail the
Helm upgrade on an immutable PVC field or, worse, succeed and quietly move
data back onto ephemeral storage — see the "Data durability" warning in
[installing.md](../release/installing.md). This is a pre-existing property
of the `local` values inherited as-is, not something this ADR's design
introduces — but it matters more here than it did for `make local-up`,
where the target was always a disposable kind cluster.

Distribution parity is exact for the two first-party images (`project-code`,
`superset`) by construction (the version-tag/digest binding above), and for
every third-party image `olf install verify --strict` proves the running
digest matches `release/component-catalog.yaml` at release time. It cannot
prove parity for images a Helm chart pulls that the catalog does not
declare (notably the Dagster chart's own default images, always overridden
to the project-code image today, but not enumerated as a
`_IMAGE_DEPLOYMENT_SOURCES`-style registered source the way `tools/olf/olf/release.py`
registers deployed images) — `--strict` reports these as "not declared in
the manifest" rather than silently ignoring them, so the gap is visible
rather than hidden. Closing it is tracked as follow-up, not fixed here.

`olf install run`'s Phase 2 step needs a way to render Floe manifests, which
today means Docker (the default `FLOE_RUNTIME=image` runner) or a
host-installed `floe` CLI at the matching version
(`FLOE_PREFER_CLI=true`) — see `docs/release/installing.md`. This is an
unavoidable consequence of installing "all the way" rather than Phase 1
only, and is the same prerequisite `make local-up` already has. The Docker
runner's container is digest-pinned (`floe` was added to
`release/component-catalog.yaml` and `_IMAGE_DEPLOYMENT_SOURCES`) and
`run_install` threads the manifest-resolved reference into it as
`FLOE_IMAGE`, rather than the mutable version tag `floe-manifest.sh`
defaulted to before: that container gets a writable bind mount of the
entire extracted release tree, so a moved tag there could inject arbitrary
code into whatever the rest of Phase 2 subsequently reads back, with
nothing else in the install path verifying it.

An explicit `--work-dir` is bound to the first (`--kubeconfig`,
`--kube-context`, `--namespace`) it was used with (`check_work_dir_target_identity`,
recorded in a `<work-dir>/config/target-identity.json` marker) and a later
run against a different target that reuses the same directory is refused.
`default_work_dir`'s own key already prevents two different default
targets from colliding, but an explicit `--work-dir` bypasses that
derivation entirely, and the Terraform state kept under `<work-dir>/state/`
is target-scoped by nature: reused for a different `--namespace`,
`terraform apply` can delete the first installation's namespace (a name
change forces resource replacement) to create the second one; reused for a
different `--kube-context`/`--kubeconfig`, it applies stale state against
a cluster that never had those resources, orphaning the first
installation's actual running resources instead.

The marker also records the cluster's own identity (`kube-system`'s
namespace UID, resolved via `resolve_cluster_uid`), not just the
kubeconfig/context/namespace pointer: the pointer alone cannot tell a
reused cluster from one deleted and recreated under the same kubeconfig
path and context name (e.g. `kind delete cluster --name X && kind create
cluster --name X`), and `kube-system` gets a fresh UID on every cluster
creation. A pointer mismatch is rejected immediately, with no cluster
contact at all; only once the pointer matches — meaning this run is about
to operate against whatever cluster it currently resolves to — is that
cluster's identity resolved and compared, catching the recreated-cluster
case the pointer alone would silently match.

The documented standalone `terraform destroy` teardown
(`docs/release/installing.md`) must re-run `terraform init` with the same
`-backend-config="path=<work-dir>/state/terraform.tfstate"` `olf install
run` used before destroying anything — that state is no longer inside the
directory `terraform -chdir` points at, so Terraform cannot find it
without being told where it is, the same way `olf install run` already is
via `TERRAFORM_STATE_PATH`.

`scripts/release/verify-install.sh` gained an opt-in `--consumer-install
--kube-context <ctx>` path that runs `olf install run`/`verify --strict`
against a real published tag, and `.github/workflows/checks.yml` gained a
`consumer-install` job that rehearses the same mechanism on every pull
request against a plain kind cluster with no foundation Terraform root
involved — proving the mechanism itself, not a specific published release,
on every change.

## References

- ADR 0008: two-phase deploy boundary `olf install` reuses unchanged
- ADR 0017: shared Python deploy tooling (`tools/olf`) `olf install` extends
- ADR 0022: Phase 2 catalog namespace reconciliation, run by `olf install`'s
  artifact-deploy step exactly as `make local-up` runs it
- `docs/release/releasing.md`: the release pipeline this ADR's digest-pinning
  guarantee depends on
- `docs/release/installing.md`: the consumer-facing install walkthrough
