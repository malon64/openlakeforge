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

The image contains Dagster, `dagster-floe`, dlt extract code, domain-owned
product Floe contracts, generated product Floe manifests, product dbt projects,
domain Python code, and shared OpenLakeForge libraries. It intentionally does
not install the Floe CLI.
`make floe-manifest` generates the product manifests locally, bakes them into
this image for Dagster asset loading, publishes them to SeaweedFS outside
Terraform for the separate runner pod, then Dagster uses `dagster-floe` to
launch Floe Kubernetes jobs from the
manifest-declared `ghcr.io/malon64/floe:0.5.4` runner image.
