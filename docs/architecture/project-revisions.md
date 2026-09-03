# Project Revisions

A project revision is a frozen, content-addressed identity for one complete
promotable data project: descriptors, Floe contracts, dbt, Dagster
orchestration code, report assets when present, and the project-code image
digest. `olf project build` computes and publishes it; `olf project revision
inspect|verify` read it back without rebuilding source. See
[ADR 0012](../adr/0012-project-revisions-and-promotion.md) for the decision
this implements, and [`#154`](https://github.com/malon64/openlakeforge/issues/154)
for the issue that introduced it.

This is a different object from `olf floe revision`, the v0.2 immutable
Floe **runtime-artifact** revision. That one identifies one stage's rendered
manifest set after a provider contract has resolved it. A project revision
identifies the project **before** any stage exists.

## Build once, promote unchanged

| Concept | Answers |
| --- | --- |
| Project revision | What does this project contain, and what image runs it? |
| Deployment Profile (`openlakeforge.yaml`) | Where does it run, and which stages are enabled? |
| Floe runtime-artifact revision | What did Floe render for one stage's contract environment? |

Promotion (`#115`) activates one already-built project revision in DEV,
validates it, then activates the same revision — same digest, no rebuild — in
optional UAT and then PROD. A project revision that changed meaning between
stages would defeat that: enabling UAT, or moving a project to another
provider, must never invalidate a revision that already exists.

## Stage activation

```bash
olf platform apply -f openlakeforge.yaml
olf project image  -f openlakeforge.yaml
olf project build  --project . --image <repository>@sha256:<digest>
olf project deploy -f openlakeforge.yaml --stage dev --revision sha256:<digest>
olf project deploy -f openlakeforge.yaml --stage prod --revision sha256:<digest>
olf project status -f openlakeforge.yaml --json
```

`olf project image` builds and pushes the project-code image and prints the
digest-pinned reference `olf project build` requires.

A revision is identified partly by that image, so the reference has to mean the
same thing from every stage -- which makes a **registry** part of the contract,
not an optional convenience. A registry digest is the only pullable identity an
image has; a local image's config Id is not one. Cloud providers use the
foundation's own ECR/ACR. Local has no registry of its own and deliberately
does not stand one up: it pushes to whatever `PROJECT_CODE_IMAGE_REPOSITORY`
names, GHCR or Docker Hub by default. See
[docs/setup/local.md](../setup/local.md) for that loop.

Activation verifies every object of the published `ProjectRevision`, restores it
to a temporary root, and renders Floe only from that root and the selected v3
provider contract. It writes an immutable `ProjectActivation` beneath
`activations/<stage>/revisions/sha256/` and advances `ACTIVE.json` only after
the stage's `openlakeforge-project` Helm release is ready. Project revisions
remain equal across stages; activation and Floe revisions intentionally differ.

## What is frozen

| Component | Source | Notes |
| --- | --- | --- |
| `descriptors` | `lakehouse_code/lakehouse.yaml`, `bronze/<source>/source.yaml` | Provider-neutral project truth |
| `floe` | `silver/<domain>/contracts/floe/<domain>.yml` | The contract, never the rendered manifest |
| `dbt` | `gold/<product>/dbt/**` | Excludes `target/` and `dbt_packages/` |
| `dagster` | `pipelines/dagster/<product>.py`, `bronze/<source>/dlt/<source>.py` | Orchestration and extract code |
| `reports` | `dashboards/superset/<dashboard>/**` | Only when at least one dashboard is declared |
| `image` | The project-code repository, plus an immutable `@sha256:` digest | A mutable tag is rejected |
| `distribution` | The running distribution's version | Gates activation against an incompatible distribution |

## What is never frozen

- `openlakeforge.yaml` — deployment intent (provider, stages, capabilities),
  not project content.
- Every stage-rendered Floe artifact: generated manifests, rendered profiles,
  and anything a provider contract resolved. These embed physical bucket
  names, Kubernetes namespaces, catalog endpoints, and Secret references —
  the opposite of what a promotable revision is allowed to contain. `#115`
  regenerates them per stage from the frozen `floe` component.
- Build output: `target/`, `dbt_packages/`, `__pycache__/`, `.venv/`.

## Building and verifying

```bash
olf project build --project . --image ghcr.io/example/project-code@sha256:<digest>
```

Refuses to build from an invalid project — `olf project build` runs the same
checks as `olf project validate` first. The image reference must already
carry a digest, or resolve to one through the image's registry `RepoDigest`
(so it has been pushed to, or pulled from, a registry); a bare mutable tag
and an unpushed local-only image are both rejected, because a revision that
could point at a moving tag, or at a digest nothing can pull, is not
actually immutable.

```bash
olf project revision verify --revision sha256:<digest>
```

Re-derives every component's digest from the published objects and compares
it against what the manifest declares — tampering with one published file, or
a partial publish that never finished, both fail closed. `verify` also checks
the manifest's recorded distribution version against the running
distribution, refusing to activate a revision built against an incompatible
one.

## Content address

Each component is a `{relative project path: sha256 digest}` map. The overall
revision hashes every `<component>/<path>` entry, sorted, through the same
primitive `olf floe revision` uses
(`revision.aggregate_revision`) — a project with no components cannot produce
a revision, and the hash is stable regardless of file discovery order or
which path on disk the project was built from.

## Publishing

`olf project build` publishes under `project/revisions/sha256/<digest>/`,
either to the ops bucket (`--via port-forward` for in-cluster S3-compatible
storage, `--via direct` for cloud S3) or to a local directory with `--output
DIR`. Every published object is immutable: republishing the same revision is
a no-op, and publishing different content under an already-published key is a
hard error rather than a silent overwrite.
