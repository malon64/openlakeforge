# Project Code Image

`images/project-code/` is the single custom runtime image boundary for v1.

The future image will be published as:

```text
ghcr.io/openlakeforge/project-code:<tag>
```

It will contain Dagster code, dagster-floe integration, Floe contracts, dagster-dbt integration, the dbt-duckdb project, dlt pipelines, domain Python code, and shared OpenLakeForge libraries.

Iteration 2 introduces the first runtime image:

```bash
make project-code-image
make project-code-load
```

The local image tag is:

```text
ghcr.io/openlakeforge/project-code:local
```

The image currently contains the minimal Sales Dagster smoke job used to prove
that Dagster can launch an isolated Kubernetes run pod through the
`K8sRunLauncher`. It does not yet contain dlt, Floe, dbt, or OpenLineage code.
