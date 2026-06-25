# Project Code Image

`images/project-code/` is the custom runtime image boundary for OpenLakeForge
domain Dagster code.

The image is built locally as:

```text
ghcr.io/openlakeforge/project-code:local
```

Generate the Floe manifests, then build and load the image into kind with:

```bash
make floe-manifest
make project-code-image
make project-code-load
```

The image contains Dagster, `dagster-floe`, dlt extract code, domain-owned
product Floe contracts, generated product Floe manifests, product dbt projects,
domain Python code, and shared OpenLakeForge libraries. It intentionally does
not install the Floe CLI.
The base image is configurable with `PROJECT_CODE_PYTHON_BASE_IMAGE`; AWS
builds default this to `public.ecr.aws/docker/library/python:3.12-slim` to avoid
Docker Hub during `make aws-artifacts-deploy`.
`make floe-manifest` generates the product manifests locally, bakes them into
this image for Dagster asset loading, and publishes the same files to SeaweedFS
outside Terraform for separate runner pods.

Dagster uses `dagster-floe` in remote manifest mode for Kubernetes:
`OPENLAKEFORGE_FLOE_MANIFEST_ACCESS_MODE=remote`. The Dagster code server reads
the local manifest from this image to build deterministic asset definitions,
while the manifest-declared `ghcr.io/malon64/floe:0.5.4` runner image receives
the corresponding `s3://openlakeforge-ops/floe/manifests/...` manifest URI at runtime.
`local` manifest mode is only valid when Floe runs in the same container as
Dagster.
