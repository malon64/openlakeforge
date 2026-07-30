<!--
This file is generated from release/component-catalog.yaml. Do not hand-edit
the tables below — regenerate with:

    olf release compatibility-matrix --output docs/release/compatibility-matrix.md

(or `uv run --project tools/olf olf release compatibility-matrix --output ...`
from the repo root). The same command produces the copy embedded in every
release bundle by .github/workflows/release.yml, so this file always matches
what a tagged release publishes as of the last catalog update. `make
release-check` / `make check-components` do not currently fail on drift
between this file and the catalog; regenerate it whenever
release/component-catalog.yaml changes.
-->

# OpenLakeForge 0.1.0-alpha.1 compatibility matrix

Generated from `release/component-catalog.yaml`. Every version below is the exact input pinned for this release; see [docs/release/component-catalog.md](component-catalog.md) for the update process.

## Platform

| Component | Required version |
| --- | --- |
| Terraform | >= 1.6.0 |

## Terraform providers

| Provider | Version |
| --- | --- |
| hashicorp/aws | 5.100.0 |
| hashicorp/azurerm | 4.77.0 |
| hashicorp/helm | 3.2.0 |
| hashicorp/kubernetes | 2.38.0 |
| hashicorp/random | 3.9.0 |
| hashicorp/tls | 4.3.0 |

## Helm charts

| Chart | Version |
| --- | --- |
| dagster | 1.13.6 |
| openmetadata | 1.12.10 |
| openmetadata-dependencies | 1.12.10 |
| polaris | 1.4.1 |
| seaweedfs | 4.23.0 |
| superset | 0.15.5 |
| trino | 1.42.2 |

## Container images

| Image | Reference |
| --- | --- |
| openmetadata_ingestion | `docker.getcollate.io/openmetadata/ingestion-base:1.12.10@sha256:dadd44b28cc73488a943009c22da7b3c7a9e52d2adb47e61ed2c5ba791e2a07d` |
| polaris | `apache/polaris:1.4.0@sha256:ef4947a3fd005ca5b2aec2bde98682a59996d38f21c16c4660fbb79e4c20b40c` |
| postgres | `postgres:16-alpine@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777` |
| project_code_base | `python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de` |
| seaweedfs | `chrislusf/seaweedfs:4.23@sha256:c6d6fb84b081f1f09bb089184ff4b45d2f163a1bfa8b354d04cf400c6e06f242` |
| superset_base | `apache/superset:6.1.0@sha256:fb3464528ec7076f91195f0ff7835755aa023e281f1bb78a84782ce7a36b3705` |
| trino | `trinodb/trino:480@sha256:1565e8cac299a32dd9177a4da2d748da4ceb9f1560a9c409d1d18fd72ea5253e` |

## Cloud services (deployment targets)

| Target | Kubernetes foundation | Object storage | Catalog | Managed database |
| --- | --- | --- | --- | --- |
| Local | kind | SeaweedFS (in-cluster) | Polaris (in-cluster) | PostgreSQL (in-cluster) |
| Azure POC | AKS | SeaweedFS (in-cluster) | Polaris (in-cluster) | PostgreSQL (in-cluster) |
| AWS POC | EKS | S3 | AWS Glue | RDS PostgreSQL |

## Supported upgrade paths

OpenLakeForge is in the Alpha lifecycle stage (see [docs/industrialization-roadmap.md](../industrialization-roadmap.md), "Lifecycle Definitions"): breaking changes are allowed between alpha releases, with migration notes published in `CHANGELOG.md` for every tag. Until Beta, only the latest alpha tag is maintained; there is no supported upgrade path guarantee prior to `v0.1.0-alpha.1`.
