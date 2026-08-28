# ADR 0012: Project revisions — a build-once, content-addressed promotion unit

## Status

Binding.

## Context

v0.3 (`#111`) promotes one immutable project revision unchanged across shared
DEV, optional UAT, and PROD (`#154`). v0.2 already publishes an immutable
revision — `tools/olf/olf/revision.py`, now `olf floe revision`. That revision
covers rendered Floe runtime artifacts only: configs, profiles, and generated
manifests, hashed after the Floe CLI renders them from the live provider
contract environment.

That rendered output is stage-bound. A generated manifest embeds
`report_base_uri: s3://<ops-bucket>/floe/reports/<domain>`, the Kubernetes
`namespace`, catalog endpoints, and Kubernetes Secret names — see
`lakehouse_code/silver/sales/contracts/floe/manifests/sales.manifest.json`.
Hashing that output would put target-stage physical endpoints inside a
manifest #154 requires to promote unchanged between stages, and a manifest
generated for DEV cannot be replayed unmodified in PROD.

The project-code image has the same problem one layer down.
`images/project-code/Dockerfile` bakes `FLOE_MANIFEST_REVISION` into
`OPENLAKEFORGE_FLOE_MANIFEST_REVISION_BUILT`, and `libs/product_dagster.py`
resolved the remote Floe manifest URI from that baked value alone. Because the
Floe revision is stage-bound, the image built against it was too — "the same
project revision digest deploys to DEV and PROD without a rebuild" was
unreachable.

## Decision

### A project revision freezes inputs, never rendered stage output

`ProjectRevision` (`tools/olf/olf/project_revision.py`) is a second,
independently addressed revision covering the complete promotable project:

| Component | Source | Excluded because |
| --- | --- | --- |
| `descriptors` | `lakehouse_code/lakehouse.yaml`, `bronze/<source>/source.yaml` | — |
| `floe` | `silver/<domain>/contracts/floe/<domain>.yml` | — |
| `dbt` | `gold/<product>/dbt/**` (no `target/`, `dbt_packages/`) | build output, not input |
| `dagster` | `pipelines/dagster/<product>.py`, `bronze/<source>/dlt/<source>.py` | — |
| `reports` | `dashboards/superset/<dashboard>/**`, when any dashboard exists | — |
| `image` | The project-code repository plus an immutable `@sha256:` digest | mutable tags are rejected |
| `distribution` | `release/component-catalog.yaml` distribution version | compatibility gate at activation |

`openlakeforge.yaml` (the Deployment Profile) and every stage-rendered Floe
artifact — manifests, profiles, and any URI a provider contract resolves — are
never in a project revision. Enabling UAT, or moving a project between
providers, must not invalidate an already-built revision; and a revision that
embedded a rendered artifact could not be replayed against a different stage's
contract environment, which is exactly what `#115` needs it to do.

The Floe runtime version (`FLOE_VERSION`/`FLOE_IMAGE`, defaulted in
`olf/floe.py`) is a distribution-level pin, not project content — it is
already covered by the `distribution` component and is not duplicated inside
`floe`.

### Content address, publish, and verify reuse the v0.2 primitives

Each component is a `{relative project path: sha256}` map. The aggregate
revision is `revision.aggregate_revision` (`olf/revision.py`) over the
flattened, `<component>/<path>`-namespaced entries — the same sorted
`key\0digest\n` hash the Floe revision already uses, so an empty project and a
tampered entry set fail the same way. Publication reuses the same
immutable-write rule: an existing key with different content is a hard
collision error, never a silent overwrite.

`tools/olf/olf/artifact_store.py` extracts the bucket/client resolution both
revisions publish through (`RevisionStore`, `S3RevisionStore` for in-cluster
or direct cloud S3, `FilesystemRevisionStore` for `olf project build
--output DIR`) so publishing does not require two client builders. Folding
`olf/revision.py` itself onto `RevisionStore` is deliberate follow-up, not
part of this record — see `docs/technical-debt.md`.

### Validation is layered the same way as descriptors and profiles

`ProjectRevisionManifest` has a hand-rolled model (`to_json`/`from_json`/
`validate`) for actionable, fail-closed errors, and
`docs/schema/project-revision.schema.json` for the versioned, tool-checkable
shape (ADR 0005, ADR 0011). `olf project build` runs both: the manifest must
round-trip through its own aggregate check, must contain no stage-bound value
(`s3://`, `http(s)://`, `AWS_*`, `*SECRET*` — the mechanical form of "no
credentials or target-stage endpoints"), and must validate against the schema
before it is published.

### The project-code image is decoupled from the Floe revision

`libs/product_dagster.py`'s manifest-URI resolution now prefers the
stage-activated runtime variable (`OPENLAKEFORGE_FLOE_MANIFEST_REVISION`,
already passed by the Dagster Terraform module on every deploy, default
`"manual"`) over the value baked into the image at build time
(`OPENLAKEFORGE_FLOE_MANIFEST_REVISION_BUILT`). The baked value remains a
fallback for the current single-stage deploy paths, which do not yet wire the
runtime variable — that wiring, from the platform root at activation time, is
`#115`'s job. This record only removes the build-time coupling that would
have blocked it: without it, one project-code image digest could never serve
more than one Floe revision, no matter what `#115` wires.

The resolution logic itself (`_built_manifest_revision`, `_revision_digest`)
moved to `libs/floe_revision.py`, free of the Dagster/Floe imports
`libs/product_dagster.py` carries, so it is unit-testable from `tools/olf`
without the isolated dependency environment `olf check project-code` builds.

### CLI surface

| Command | Behavior |
| --- | --- |
| `olf project build --project P --image REF` | Validate the project, freeze its content, resolve/require a digest-pinned image, publish |
| `olf project revision inspect --revision R` | Read a published manifest without rebuilding source |
| `olf project revision verify --revision R` | Re-verify manifest self-consistency, the distribution-compatibility gate, and every published object's digest |

`olf revision` (the v0.2 Floe runtime-artifact commands) moved to `olf floe
revision compute|publish|activate|verify`, freeing the top-level name for the
project revision and keeping "which revision" unambiguous from the command
path alone.

## Consequences

- `#115` consumes `ProjectRevision.project_code_image` and per-component
  digests to activate a stage without ever rebuilding from source, and can
  render Floe manifests against the frozen `floe` component's contracts for
  the stage it is activating.
- A revision built once is byte-identical regardless of which stage it is
  later activated in, or which path on disk the project was built from —
  `openlakeforge.yaml` and every rendered artifact are outside its content
  address by construction.
- The v0.2 Floe runtime-artifact revision is unchanged in meaning: it still
  identifies one stage's rendered artifact set, and `#115` still activates it
  per stage. `ProjectRevision` does not replace it; it is the layer above.

## History

New record. No prior ADR modeled a promotable project revision distinct from
the v0.2 Floe runtime-artifact revision; ADR 0009's distribution-payload
identity and ADR 0011's Deployment Profile are the two existing typed
identities this one composes with, not supersedes.
