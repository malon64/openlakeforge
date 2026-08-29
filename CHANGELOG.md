# Changelog

All notable changes to OpenLakeForge are documented here. This project is in
the **Alpha** lifecycle stage (see
[docs/industrialization-roadmap.md](docs/industrialization-roadmap.md),
"Lifecycle Definitions"): breaking changes are allowed between alpha
releases, with migration notes recorded below for every tag. Until Beta,
only the latest alpha tag is maintained.

Release tags are create-only semantic versions (`release_tag_policy:
immutable-semver` in `release/component-catalog.yaml`) and are never
force-updated. See [docs/release/releasing.md](docs/release/releasing.md)
for how a release is cut and verified.

## [Unreleased]

### Added

- The local platform Terraform root is stage-aware: it takes the resolved
  `DeploymentTopology` as typed inputs and provisions one shared `olf-system`
  namespace plus one `olf-<stage>` namespace, runtime service account, and
  isolated metadata database per enabled stage. Dagster and Superset are
  `for_each` instances over the enabled stages instead of single module
  blocks; PostgreSQL, SeaweedFS, Polaris, Trino, and OpenMetadata keep exactly
  one Terraform owner (#133, ADR 0002).
- `olf deploy`/`plan`/`status`/`forward`/`e2e run` take `--stage` (default
  `dev`), and `olf deploy`/`plan` take `--allow-stage-removal`: an apply that
  would drop an already-deployed stage — destroying its namespace, services,
  and metadata state — now fails closed without it (#133).

- `olf project build --project P --image REF` computes and publishes an
  immutable, content-addressed `ProjectRevision` covering descriptors, Floe
  contracts, dbt, Dagster orchestration code, report assets when present,
  the project-code image digest, and the distribution version; `olf project
  revision inspect|verify` read a published revision without rebuilding
  source (#154, ADR 0012).

### Changed

- The local stack no longer runs in one `lakehouse` namespace. Shared services
  move to `olf-system` and stage services to `olf-<stage>`, and every service
  endpoint in the provider contract is namespace-qualified. Upgrading a v0.2
  local deployment means `olf destroy --provider local` followed by a fresh
  deploy -- destroying first is what releases the cluster-scoped objects a
  chart owns (SeaweedFS' ClusterRole among them), which a new release in
  another namespace cannot adopt; the `--namespace` option now only
  overrides the stage namespace, and
  the `aws-poc`/`azure-poc` roots keep their single `lakehouse` namespace
  until #114 (#133).
- `--profile slim|full` is now an explicit, deprecated single-DEV shorthand.
  With no `--profile`, the project-root `openlakeforge.yaml` Deployment
  Profile decides which stages and capabilities are deployed; a project with
  no profile file falls back to the same single-DEV shorthand, and an invalid
  profile fails closed (#133, ADR 0011).
- `olf.contracts.load_provider_contracts` returns a provider-contract v3
  payload instead of refusing it; `build_contract_env` resolves it against a
  `DeploymentTopology` and an explicit stage (#133, ADR 0003).
- `olf revision compute|publish|activate|verify` (the v0.2 Floe
  runtime-artifact revision) moved to `olf floe revision ...`, freeing the
  top-level `revision` name for the new project revision.
- `libs/product_dagster.py` prefers the stage-activated runtime
  `OPENLAKEFORGE_FLOE_MANIFEST_REVISION` over the value baked into the
  project-code image at build time, so one image digest no longer requires
  a rebuild per Floe revision (#154).

## [0.2.0-alpha.1] - 2026-08-26

The small-team adoption release (Milestone 2): OpenLakeForge installs from
PyPI without a checkout, scaffolds a data product without touching shared
platform code, and runs a slim profile with no product allowlist baked into
the tooling.

### Added

- PyPI distribution: `pip install openlakeforge` installs the `olf` console
  command with a verified, immutable Terraform/Helm/runtime payload embedded
  in the wheel and sdist (#128, ADR 0009).
- `olf init` bootstraps a writable `lakehouse_code/` project from the
  packaged demo in the current directory; `olf init --empty` creates a
  transitional project with no source, domain, or product yet (#146,
  ADR 0009).
- A managed Terraform/Helm/kubectl/kind toolchain: `olf` downloads, verifies,
  and privately invokes its own versioned copies under `OLF_HOME`, so none of
  those tools need to be installed on the host. `OLF_TOOLCHAIN_MODE=host`
  opts back into host-installed copies (#127, ADR 0008).
- SDK-managed AWS and Azure authentication: `olf auth login --provider
  aws|azure` goes through boto3 / the Azure SDK directly; the `aws` and `az`
  CLIs are no longer required (#142, ADR 0008).
- Golden-path scaffolding: `olf source new`, `olf domain new`, and
  `olf product new` generate a runnable Bronze source, Silver domain, or Gold
  product from documented inputs, with no shared-code edit (#40).
- A typed domain inventory built from validated descriptors; every seed-
  product allowlist is gone from shared platform code, so an added product
  is discovered automatically (#39).
- Persistent Polaris catalog state: a Polaris pod restart no longer loses
  table identity or requires a full platform re-apply (#79).
- A slim local profile that omits OpenMetadata and Superset, with e2e
  assertions skipped rather than failed when a layer is absent (#78).
- A kind smoke gate (`olf smoke run`) on every pull request: one product
  pipeline through to a queryable Gold table, within a 45-minute budget
  (#81).

### Changed

- `olf` is now the only repository orchestration implementation; the
  shell-scripted deploy path is gone. `Makefile` targets are deprecated
  one-line delegates to the equivalent `olf` command (#122-#126, ADR 0008).
- Dagster collapses to one merged code location by default; a per-domain
  split is now an explicit configuration choice rather than the default
  (#76, ADR 0006).
- `lakehouse_code/` replaces `domains/` as the user-code root: Bronze is
  source-owned, Silver is domain-owned, and Gold stays product-owned (#109,
  ADR 0004). The `openlakeforge.io/v1alpha3` `Lakehouse`/`Source` descriptor
  pair replaces `v1alpha1`/`v1alpha2` `Domain` descriptors; their loader and
  validator are removed along with `docs/schema/domain*.json` and the
  `v1alpha1`->`v1alpha2` migration guide.
- `scripts/release/verify-install.sh` is replaced by `olf release
  verify-install`.
- The decision log is consolidated from 32 ADRs to 10, renumbered `0001`-`0010`
  and rewritten to describe what binds today rather than stacking supersessions.
  Each new ADR carries a History footer naming the records it absorbs. ADR
  numbers referenced in earlier releases, commits, and closed pull requests
  refer to the old numbering.

### Migration notes

Consumers upgrading from `v0.1.0-alpha.1` should:

1. Stop cloning the repository to install: `pip install openlakeforge` (or
   `uv tool install openlakeforge --python 3.12`), then `olf init` in an
   empty project directory. See the [README](README.md#quick-start) and
   [local installation guide](docs/setup/local.md).
2. Migrate any `domains/<domain>/domain.yaml` descriptor to
   `lakehouse_code/lakehouse.yaml` plus one `lakehouse_code/bronze/<source>/
   source.yaml` per Bronze source (`openlakeforge.io/v1alpha3`). See
   [docs/reference/domain-descriptor.md](docs/reference/domain-descriptor.md).
3. Replace direct `scripts/*.sh` or checkout `make <env>-*` invocations with
   the equivalent `olf` command; every `Makefile` target still works as a
   delegate, but is no longer the primary interface.
4. Expect further breaking changes in the next alpha; this stage carries no
   forward compatibility guarantee.

### Known limitations

- No stable support window is published before `v1.0`; only the latest
  alpha tag is maintained.
- The Azure POC and AWS POC deployment targets remain proof-of-concept
  scope; see `docs/architecture/aws-eks-poc.md` for the current AWS
  compatibility gate.

## [0.1.0-alpha.1] - 2026-08-10

The first publishable OpenLakeForge alpha: a signed, SBOM'd, provenance-
attested release bundle built from the seed multi-product POC (Sales
`order_revenue` and `customer_health`, Supply Chain
`inventory_reliability`) across the local (kind), Azure POC (AKS), and AWS
POC (EKS) deployment targets.

### Added

- `.github/workflows/release.yml`: tag-triggered (`v*`) and
  `workflow_dispatch` (dry-run capable) release pipeline. Builds and pushes
  `project-code` and `superset` images to `ghcr.io/malon64/openlakeforge/*`
  by digest, signs them keylessly with cosign (Sigstore OIDC, no long-lived
  keys), generates an SPDX SBOM per image attached as both a cosign
  attestation and a release asset, attaches SLSA build provenance via
  `actions/attest-build-provenance`, and publishes the GitHub Release with
  the changelog, the component manifest, the compatibility matrix, and
  `checksums.txt`.
- `olf release` CLI group (`tools/olf/olf/release.py`): `manifest`,
  `checksums`, `compatibility-matrix`, and `check` (the release-readiness /
  clean-install consistency gate).
- `make release-check` and `make release-bundle` targets, and a
  `release-check` job in `.github/workflows/checks.yml` so release drift is
  caught on every pull request, not only at tag time.
- `docs/release/releasing.md` and `docs/release/compatibility-matrix.md`.
- `scripts/release/verify-install.sh`: the scripted clean-checkout install
  verification a consumer (or the maintainer, post-merge) runs against a
  published tag.

### Migration notes

This is the first tagged release; there is no prior version to migrate
from. Consumers adopting this alpha should:

1. Pin to the immutable tag `v0.1.0-alpha.1` (or the resolved image
   digests recorded in the release's `component-manifest.json`) rather than
   `:local` or `main`.
2. Follow [docs/release/releasing.md](docs/release/releasing.md) to verify
   signatures and checksums before deploying.
3. Expect breaking changes in the next alpha; this stage carries no forward
   compatibility guarantee (see "Lifecycle Definitions" in
   `docs/industrialization-roadmap.md`).

### Known limitations

- No stable support window is published before `v1.0`; only the latest
  alpha tag is maintained.
- The Azure POC and AWS POC deployment targets remain proof-of-concept
  scope; see `docs/architecture/aws-eks-poc.md` for the current AWS
  compatibility gate.
