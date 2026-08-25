# olf — OpenLakeForge deployment tooling

`olf` is the supported OpenLakeForge interface. It owns repository
orchestration for local, AWS, and Azure: Terraform/Helm sequencing, provider
contract hydration, artifacts, checks, diagnostics, and release helpers.
Terraform remains the state/drift engine and Helm remains the chart/release
engine; `olf` invokes both with structured argv, retries, and diagnostics. See
[ADR 0028](../../docs/adr/0028-python-owns-repository-orchestration.md).

`olf` also owns its own versioned Terraform, Helm, kubectl, and kind under
`OLF_HOME` (default `~/.openlakeforge`) - a host installation of those tools
is not required. `OLF_TOOLCHAIN_MODE=host` resolves them from `PATH` instead.
See [ADR 0029](../../docs/adr/0029-olf-owns-a-managed-toolchain.md).

## Commands

| Command | Purpose |
| --- | --- |
| `olf doctor --provider P [--phase PHASE]` / `olf plan --provider P [--phase PHASE]` | Read-only preflight and Terraform planning with typed provider/profile/phase options; provisions the managed toolchain as a side effect. |
| `olf deploy\|destroy\|status\|forward --provider P` | Orchestrate a provider lifecycle without shell wrappers. |
| `olf toolchain list\|install\|path\|clean` | Inspect, provision, or remove the managed Terraform/Helm/kubectl/kind toolchain under `OLF_HOME`. |
| `olf check structure\|components\|contracts\|infra\|project-code\|dbt\|lockfiles\|all` | Repository validation gates. |
| `olf images build\|load project-code\|superset` | Local Docker build and Kind load operations. |
| `olf diagnostics collect OUTPUT_DIR` | Collect bounded host, Docker, Kubernetes, event, and pod-log evidence. |
| `olf catalog sync-namespaces [--dry-run] [--prune]` | Reconcile catalog namespaces (Polaris) or databases (Glue) with domain descriptors (ADR 0022). Runs first in `artifacts-deploy`, before any table is written. It creates, adopts matching legacy namespaces, and relocates OpenLakeForge-managed namespaces. `--prune` removes only OpenLakeForge-managed catalog metadata and retains object-store files; foreign namespaces are never changed. Unsupported providers fail explicitly. |
| `olf floe render-profile` | Render the Floe EnvironmentProfile YAML for the active contract env. |
| `olf artifacts upload-manifests --via port-forward\|direct` | Publish domain Floe manifests to the ops bucket (in-cluster S3 or cloud S3). |
| `olf revision compute\|publish\|verify --runtime-root D` | Compute, publish, or verify an immutable Floe runtime-artifact revision. Publication writes `floe/revisions/sha256/<digest>/...` and does not activate that revision. |
| `olf superset deploy-reports` / `export-reports` | Build/import or export Superset report bundles. |
| `olf openmetadata deploy-metadata` | Seed OpenMetadata domains, data products, and medallion containers over REST. |
| `olf k8s set-project-code-image --image X` | Point every Dagster surface at a pushed project-code image, trigger one coordinated restart, and wait for its rollout. |
| `olf e2e run --env local\|azure\|aws [--suite full\|smoke]` | Run shared end-to-end validation. All environments default to `full`; use `--suite smoke` for preflight-only checks. |

## Development

```sh
uv sync --project tools/olf
uv run --project tools/olf pytest
uv run --project tools/olf ruff check tools/olf
```

Make is optional, deprecated checkout compatibility. Its targets are one-line
delegates to the same commands and are not the supported product interface.
