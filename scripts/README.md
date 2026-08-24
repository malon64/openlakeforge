# Script migration map

There are intentionally no tracked shell scripts in OpenLakeForge. Repository
orchestration is owned by the uv-managed Python CLI:

| Former script area | `olf` replacement |
| --- | --- |
| `scripts/test/` | `olf check structure|components|contracts|infra|project-code|dbt|lockfiles|all` |
| `scripts/artifacts/` | `olf floe generate-manifests`, `olf dbt parse`, and artifact command groups |
| `scripts/local/images/` | `olf images build|load project-code|superset` |
| `scripts/contracts/` | Provider contract hydration inside the command that needs it |
| `scripts/ci/` | `olf diagnostics collect` |
| `scripts/release/` | `olf release build-bundle` and `olf release verify-install` |

Make remains only as deprecated checkout compatibility. Each target delegates
directly to one `olf` command; use `olf` in documentation, automation, and new
work. Terraform remains the state and drift engine, while Helm remains the
chart/release engine. `olf` sequences both engines and owns their process
environment, retries, preflight, and diagnostics.
