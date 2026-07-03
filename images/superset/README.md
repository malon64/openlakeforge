# Superset Image

`images/superset/` is the local Superset runtime image boundary.

The image is built locally as:

```text
ghcr.io/openlakeforge/superset:local
```

Build and load it into kind with:

```bash
make superset-image
make superset-load
```

It extends the official Superset image with the Trino Python driver so Superset
can query product Gold Iceberg marts through Trino.
The base image is configurable with `SUPERSET_BASE_IMAGE`; build scripts pull it
with retries before running `docker build`.

The image also patches Superset's Trino engine spec for local Iceberg previews.
Superset otherwise treats Trino Iceberg `$partitions` metadata columns as table
partition columns and generates invalid SQL Lab preview filters.
