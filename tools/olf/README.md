# olf — OpenLakeForge deployment tooling

`olf` is the shared, uv-managed Python package for the cross-environment logic
in the OpenLakeForge deploy pipeline. Shell scripts stay the orchestrators for
CLIs (terraform, kubectl, helm, docker, dbt, floe, aws, az); `olf` owns the work
that is the same across local, Azure, and AWS: REST/API calls, object-storage
uploads, report bundle manipulation, credential handling, and provider-contract
parsing. See [ADR 0017](../../docs/adr/0017-shared-python-deploy-tooling.md).

## Commands

| Command | Purpose |
| --- | --- |
| `olf contracts env [--terraform-dir D]` | Resolve the provider-contract runtime environment to `export`/`unset` lines (sourced by `scripts/contracts/load-runtime-env.sh`). |
| `olf floe render-profile` | Render the Floe EnvironmentProfile YAML for the active contract env. |
| `olf artifacts upload-manifests --via port-forward\|direct` | Publish product Floe manifests to the ops bucket (in-cluster S3 or cloud S3). |
| `olf superset deploy-reports` / `export-reports` | Build/import or export Superset report bundles. |
| `olf openmetadata deploy-metadata` | Seed OpenMetadata domains, data products, and medallion containers over REST. |
| `olf k8s set-project-code-image --image X` | Point every Dagster surface at a pushed project-code image. |
| `olf k8s restart-dagster` | Restart Dagster webserver, daemon, and domain code-location deployments. |
| `olf polaris check-credentials` | Preflight Polaris service-principal credentials (exit 3 = stale). |

## Development

```sh
uv sync --project tools/olf
uv run --project tools/olf pytest
uv run --project tools/olf ruff check tools/olf
```

Shell reaches these commands through `scripts/lib/python.sh` (`olf_run`), which
wraps `uv run --project tools/olf`. `uv` is the only added host prerequisite.
