# Project Code Image

`images/project-code/` is the custom runtime image boundary for OpenLakeForge
domain Dagster code.

The image is built locally as:

```text
ghcr.io/openlakeforge/project-code:local
```

Build and load it into kind with:

```bash
make floe-manifest
make project-code-image
make project-code-load
```

The image contains Dagster, `dagster-floe`, dlt extract code, Floe contracts,
domain Python code, and shared OpenLakeForge libraries. It intentionally does
not install the Floe CLI and does not include generated Floe manifests. Iteration
3 generates the Sales Floe manifest locally, Terraform uploads it to SeaweedFS,
then Dagster uses `dagster-floe` to launch Floe Kubernetes jobs from the
manifest-declared `ghcr.io/malon64/floe:0.4.4` runner image.
