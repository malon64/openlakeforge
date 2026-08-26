# ADR 0032: The installed project root is the current directory

## Status

Accepted. Completes ADR 0031's project/distribution split by naming what an
installed `olf` treats as the project root. It does not change ADR 0008's
deploy phases, the provider adapter boundary, or the v1alpha3 schema.

## Context

ADR 0031 separated the immutable platform payload from the project, state,
work, and cache roots, but the installed layout still resolved the project
root to the payload itself. Consumer commands therefore read and wrote user
descriptors inside a directory that is verified, shared between projects, and
deliberately read-only — the bundled demo could only be used by pointing every
command at an explicit `--project-root`.

Issue #146 adds `olf init`, which turns an ordinary directory into a writable
project. That only works if the commands run afterwards agree on where the
project is, and it produces a second problem: `olf init --empty` writes a
project that has no source and no product yet, which the v1alpha3 descriptor
contract requires.

## Decision

- An installed distribution resolves `project_root` to the current working
  directory. `OPENLAKEFORGE_PROJECT_ROOT` and the `--project-root`/`--repo-root`
  options still override it. A source checkout keeps the checkout itself as
  the contributor default; the two modes stay distinct.
- User material — descriptors, Floe contracts, dashboards, generated project
  code — resolves from `project_root`. Platform material — Terraform, Helm,
  schemas, Dockerfiles, shared libraries, and the release catalog — resolves
  from `distribution_root`. Scaffolding therefore validates user descriptors
  against schemas that live outside the project.
- `olf init` copies the packaged demo `lakehouse_code` into the project, or
  writes a transitional skeleton under `--empty`. It stages into a sibling
  directory and renames atomically, and it refuses to overwrite an existing
  `lakehouse_code`.
- The v1alpha3 schema is unchanged. A transitional loader waives exactly three
  cardinality rules — empty `sources`, empty `domains`, and no product in any
  domain — and nothing else. Only `olf source new` and `olf domain new` may use
  it. `olf product new`, `olf check`, `olf doctor`, deploy, and e2e stay
  strict, so the first product is what makes a project deployable.

## Consequences

`mkdir my-lakehouse && cd my-lakehouse && olf init` is the supported consumer
path, and no subsequent command needs `--project-root .`. An `olf init --empty`
project is reported as not runnable by `olf doctor` and refused by the dynamic
artifact deploy phase until it has a source and a product; the message names
what is missing. Contributor checkouts are unaffected.
