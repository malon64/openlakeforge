# Installing OpenLakeForge

This is the first-time consumer path: install a tagged, verified release into
a Kubernetes cluster you already have, with no repository clone. If you are
developing OpenLakeForge itself, use `make local-up` instead — see the
[README](../../README.md) and [AGENTS.md](../../AGENTS.md).

See [releasing.md](releasing.md) for how a release is built and how to verify
its signatures and checksums directly; this page covers applying one.

## ⚠ Data durability

`olf install` deploys `infra/helm/values/local` unchanged — the same values
`make local-up` uses for a throwaway kind cluster. In particular,
**SeaweedFS's object storage (master, volume, and filer data — this is
where the Bronze/Silver/Gold Iceberg tables actually live) is configured as
`emptyDir`, not a `PersistentVolumeClaim`.** On a real, long-lived cluster,
any pod restart, eviction, or node drain for those pods deletes all lakehouse
data with no recovery path. PostgreSQL (Dagster and OpenMetadata metadata)
*is* PVC-backed by default, independent of `emptyDir`.

There is currently no persistent-storage profile for `olf install`. Before
relying on an install for anything beyond evaluation, write a values file
overriding `master.data`, `volume.dataDirs`, and `filer.data` to a
`PersistentVolumeClaim`-backed type with a `storageClass` your cluster
provides, and pass it with `--storage-values-override`:

```sh
uv run --project tools/olf olf install run \
  --tag v0.1.0-alpha.1 \
  --kube-context <your-context> \
  --profile full \
  --storage-values-override /path/to/seaweedfs-pvc-overrides.yaml
```

**The file you pass must live outside `--work-dir`** (or wherever `olf
install run` extracts the bundle into) **when you first pass it.** Editing
`infra/helm/values/local/seaweedfs.yaml` directly inside the extracted
bundle does not work: every `olf install run` deletes and re-extracts that
tree from the release archive first, silently discarding the edit before
Terraform ever runs. `--storage-values-override` is layered on top of the
bundle's `seaweedfs.yaml` at apply time instead.

**You only need to pass `--storage-values-override` once per `--work-dir`.**
On first use, its content is copied into `<work-dir>/config/` (not the
bundle's extraction tree, so it isn't wiped by the next re-extraction), and
every later `olf install run` against the same target — including an
upgrade to a different `--tag` that omits the flag — automatically reuses
that persisted copy. This matters: without persisting it, an upgrade that
forgot to repeat the flag would silently revert SeaweedFS to the bundle's
`emptyDir` values, which can fail the Helm upgrade on an immutable PVC
field or, worse, succeed and quietly move data back onto ephemeral storage.
To change the override later, pass `--storage-values-override` again with
a different file — it replaces the persisted copy.

## Prerequisites

| Tool | Used for |
| --- | --- |
| `kubectl` | talking to your cluster |
| `terraform` (>= 1.7.0) | applying the static platform (Phase 1) |
| `helm` (>= 3.1) | charts Terraform wraps |
| `uv` | running `olf`, the OpenLakeForge deployment CLI |
| `cosign` | verifying release signatures |
| `docker`, **or** a host `floe` CLI at the pinned version | rendering product Floe manifests during artifact deploy (Phase 2) |

You do **not** need a clone of the repository. `olf install run` downloads
everything it needs from the tagged GitHub Release.

A kubeconfig context that already points at your target cluster, with
permission to create namespaces, workloads, and secrets in it.

## 1. Get `olf`

`olf` lives in `tools/olf` and is not (yet) published as a standalone
package. Until it is, fetch the release's `install-bundle.tar.gz` and run it
from there. **Verify it before running anything from it** — `install-bundle.tar.gz`
is code you are about to execute, and it is exactly the kind of asset the
signed checksum manifest exists to protect: verifying *after* extracting and
running `olf` would let a tampered bundle's own code decide whether to honor
that later check.

```sh
TAG=v0.1.0-alpha.1
for asset in checksums.txt checksums.txt.bundle install-bundle.tar.gz; do
  curl -fsSL -o "${asset}" \
    "https://github.com/malon64/openlakeforge/releases/download/${TAG}/${asset}"
done

cosign verify-blob checksums.txt \
  --bundle checksums.txt.bundle \
  --certificate-identity-regexp "^https://github\.com/malon64/openlakeforge/\.github/workflows/release\.yml@refs/tags/${TAG}\$" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com

sha256sum -c <(grep install-bundle.tar.gz checksums.txt)   # or: shasum -a 256 -c ...
```

Only once that passes:

```sh
tar -xzf install-bundle.tar.gz
cd openlakeforge-0.1.0-alpha.1
uv run --project tools/olf olf --help
```

`olf install run` (next step) repeats this same verification for every asset
as part of the install — the steps above are only to get `olf` itself onto
your machine, verified, the first time. If you already have `uv` and a
verified checkout of the bundle from a previous install, you can skip
straight to step 2.

## 2. Install a release

```sh
uv run --project tools/olf olf install run \
  --tag v0.1.0-alpha.1 \
  --kube-context <your-context> \
  --profile full
```

This:

1. Downloads every asset for the tag from the GitHub Release (public, no
   credentials required).
2. Authenticates `checksums.txt` with its keyless Sigstore bundle
   (`checksums.txt.bundle`), then verifies every asset's sha256 against it.
3. Verifies the cosign signature on both published image digests
   (`project-code`, `superset`).
4. Unpacks `install-bundle.tar.gz` and applies the static platform
   (`scripts/local/stack/platform-up.sh`) with image references and
   Terraform variables resolved from `component-manifest.json` — never from
   local repository state.
5. Deploys dynamic artifacts (`deploy-artifacts.sh`): reconciles catalog
   namespaces, renders and publishes product Floe manifests, points Dagster
   and Superset at the exact cosign-verified image **digests** (not just the
   version tag Phase 1 deployed by — a moved tag would otherwise deploy
   whatever it currently resolves to, not the bytes cosign verified), and
   deploys optional-layer artifacts (Superset reports, OpenMetadata
   metadata) when enabled.

Signature and checksum verification always run; there is no flag to skip
them against a real release. (`--skip-signature-verification` exists only
for rehearsing the mechanism against an unsigned local dry-run bundle — see
`scripts/release/build-bundle.sh` — and must never be used against a
published tag.)

### `--profile`

| Profile | Governance (OpenMetadata) | Analytics (Superset) |
| --- | --- | --- |
| `full` (default) | enabled | enabled |
| `slim` | disabled | disabled |

`slim` matches `infra/terraform/environments/local/slim.tfvars` — the
fastest path to a working ingestion-to-Gold pipeline, without the governance
or dashboard layers.

### Useful flags

| Flag | Default | Purpose |
| --- | --- | --- |
| `--namespace` | `lakehouse` | Kubernetes namespace for the platform |
| `--assets-dir` | (fetch from GitHub) | use already-downloaded release assets instead |
| `--kubeconfig` | `$KUBECONFIG`, or `~/.kube/config` | kubeconfig to read `--kube-context` from |
| `--cluster-name` | `openlakeforge-install` | recorded in the foundation contract; informational |
| `--skip-artifacts` | off | apply only the static platform (Phase 1), skip Phase 2 -- also skips re-pinning project-code/Superset to their verified digest (see below) |
| `--work-dir` | `~/.openlakeforge/install/<context>-<hash>/<namespace>` | downloads, the unpacked bundle, Terraform state, and durable config |
| `--storage-values-override` | (none) | Helm values file layered on SeaweedFS's, e.g. for PVC-backed storage — see "Data durability" above |
| `--adopt-namespace` | off | take ownership of a pre-existing `--namespace` this `--work-dir` has no record of managing — see below |

`--work-dir` defaults to a stable directory keyed by your resolved
kubeconfig path, `--kube-context`, and `--namespace` (the `<hash>` covers the
raw context/kubeconfig identity, so it disambiguates both two different
kubeconfigs sharing a context name and two distinct context names that
happen to normalize to the same directory prefix) rather than a fresh one
each run, so re-running `olf install run` against the same target —
including to install a *different* tag — reuses the same Terraform state
instead of losing track of what it already deployed. It has three
subdirectories: `bundle/` (the unpacked release, deleted and re-extracted
on every run), `state/` (Terraform's state, `terraform.tfstate` — never
touched by a bundle re-extraction), and `config/` (the storage override and
target-identity marker below — also never touched by a re-extraction).

By default, `olf install run` **refuses** to apply against a `--namespace`
that already exists but that this `--work-dir` has no record of managing —
it could be unrelated to this install. Pass `--adopt-namespace` to take
ownership of it explicitly; a namespace this exact `--work-dir` already
manages from a prior run is reconciled normally either way. A namespace
that doesn't exist yet is always created without needing the flag.

If you pass `--work-dir` explicitly, it is bound to the first
(`--kubeconfig`, `--kube-context`, `--namespace`) it was used with. A later
run against a *different* target that reuses the same directory is refused:
the Terraform state inside it is target-scoped, and applying it against a
different namespace or cluster can delete the first installation's
resources (a namespace rename forces replacement) or orphan them (stale
state applied against a cluster that never had those resources). This also
catches the same kubeconfig/context/namespace now pointing at a
*recreated* cluster (e.g. after `kind delete cluster` + `kind create
cluster` with the same name) — `olf install run` records the cluster's own
identity alongside the pointer and refuses if it no longer matches.

## 3. Prove the install matches the release

```sh
uv run --project tools/olf olf install verify \
  --manifest <work-dir>/assets/component-manifest.json \
  --kube-context <your-context> \
  --kubeconfig <your-kubeconfig> \
  --profile full
```

`olf install run` prints the exact `--manifest` path to use — by default,
`~/.openlakeforge/install/<your-context>-<hash>/<namespace>/assets/component-manifest.json`
(or under `--work-dir` if you set one). Pass the same `--kubeconfig` you gave
`olf install run` if that context only exists in a non-default kubeconfig.

This reads back every running pod's *resolved* container image (`repo@sha256:...`,
the kubelet's own record of what it pulled — not the tag or digest you asked
for) and compares it against `component-manifest.json`. It fails if any
image the manifest declares for your `--profile` is missing or running at a
different digest — this always runs, and is what the command above checks.

Add `--strict` to *additionally* fail on any running image the manifest
does not declare at all. Do not add it to the command above: on the `full`
profile it currently fails against a healthy install, because some Helm
chart defaults (e.g. the OpenMetadata server) are not yet registered as
tracked deployment sources (see
[ADR 0025](../adr/0025-consumer-install-from-release-manifest.md)). Useful
as an occasional diagnostic once that gap closes, not as part of the
routine check.

## Teardown

`olf install` does not manage cluster lifecycle — it only applies Terraform
and Helm resources into the namespace and cluster you gave it. To remove
them, run `terraform destroy` from the Terraform root `olf install run`
actually applied — **not** the one-off bootstrap checkout from step 1. That
root is `<work-dir>/bundle/infra/terraform/environments/local`, where
`<work-dir>` defaults to
`~/.openlakeforge/install/<your-context>-<hash>/<namespace>` (`olf install
run` prints the exact path; `--work-dir` if you set one).

**The actual state file lives outside that root**, at
`<work-dir>/state/terraform.tfstate` — `olf install run` points Terraform at
it with `-backend-config="path=..."` on every `init`, precisely so a bundle
re-extraction on the next install or upgrade can never touch it. Reinitialize
with the same backend config before destroying, or Terraform finds no state
at its own default path and destroys nothing, silently leaving the installed
stack running:

**`<your-kubeconfig>` must be an absolute path here, even if you originally
passed a relative one to `--kubeconfig`.** `olf install run` resolves a
relative `--kubeconfig` against the directory you ran it from before ever
invoking Terraform; this standalone `terraform destroy` has no such
normalization step; and Terraform's own `abspath()` (`infra/terraform/environments/local/main.tf`)
resolves a relative `kubeconfig_path` against *its* `-chdir` directory
instead — a different, likely nonexistent path. A relative value here
fails provider initialization and leaves the stack running, not torn down.
If you only remember the relative path you used, resolve it first:
`readlink -f ./cluster.yaml` (Linux) or `python3 -c "import
sys,pathlib;print(pathlib.Path(sys.argv[1]).resolve())" ./cluster.yaml`
(portable).

```sh
TERRAFORM_DIR=<work-dir>/bundle/infra/terraform/environments/local
KUBE_CONTEXT=<your-context> KUBECONFIG=<your-absolute-kubeconfig-path> \
  terraform -chdir="${TERRAFORM_DIR}" init \
  -backend-config="path=<work-dir>/state/terraform.tfstate"
KUBE_CONTEXT=<your-context> KUBECONFIG=<your-absolute-kubeconfig-path> \
  terraform -chdir="${TERRAFORM_DIR}" destroy \
  -var="foundation_mode=existing-cluster" \
  -var="cluster_name=<your-cluster-name>" \
  -var="kube_context=<your-context>" \
  -var="kubeconfig_path=<your-absolute-kubeconfig-path>"
```

Deleting the entire cluster (if you created it solely for this install) is
simpler and equally complete.

## Troubleshooting

- **`cosign` not found** — install from
  <https://docs.sigstore.dev/cosign/installation/>.
- **Signature verification fails** — confirm you are installing a real
  published tag, not a branch or a locally-built dry run; keyless cosign
  verification only succeeds against the ref the release workflow actually
  ran from.
- **Phase 2 fails to render Floe manifests** — `docker` must be running, or
  set `FLOE_PREFER_CLI=true` with a host `floe` CLI at the pinned version
  (see `scripts/artifacts/floe-manifest.sh`).
- **`olf install verify` reports `MISSING`** — the image was expected for
  your `--profile` but never ran; check `kubectl get pods -n <namespace>`
  and `kubectl describe` the pending workload.
- **`olf install verify` reports `MISMATCH`** — the running digest does not
  match the release. This should not happen against an unmodified install;
  if it does, do not trust the running deployment and re-run `olf install
  run`.
