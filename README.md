# OpenLakeForge

OpenLakeForge is a cloud-agnostic, open-source, self-hostable modern lakehouse
platform. It assembles open-source data platform components on Kubernetes with
Terraform and Helm.

![OpenLakeForge Architecture](/docs/assets/openlakeforge_archi.png)

The v1 proof of concept focuses on a local Kubernetes lakehouse path across
multiple domain-owned data products:

```text
CSV examples
  -> Bronze landing
  -> Floe validation
  -> Silver Iceberg tables through Polaris
  -> dbt-duckdb Gold marts
  -> Trino query
  -> Superset reports
  -> Dagster asset graph
```

The current seed POC contains two Sales data products, `order_revenue` and
`customer_health`, plus one Supply Chain data product,
`inventory_reliability`.

## v1 Stack

| Layer | Choice | v1 Role |
| --- | --- | --- |
| Extraction | dlt | Default ingestion framework |
| Technical contracts | Floe | Bronze-to-Silver validation and Silver materialization |
| Transformation | dbt-duckdb | Silver-to-Gold business models |
| Table format | Apache Iceberg | Open table format |
| Catalog | Apache Polaris / AWS Glue | Polaris for local/Azure POC, Glue for AWS POC |
| Object storage | SeaweedFS / S3 | SeaweedFS for local/Azure POC, S3 for AWS POC |
| Query serving | Trino | Analytics query engine |
| Reporting | Superset | BI reports over Gold marts |
| Orchestration | Dagster | Asset graph and run orchestration |

## Deployment Targets

| Target | Foundation | Managed-service replacements |
| --- | --- | --- |
| Local | kind | None; SeaweedFS, PostgreSQL, and Polaris run in-cluster |
| Azure POC | AKS + ACR | None yet; Azure proves AKS/ACR parity while keeping in-cluster services |
| AWS POC | EKS + ECR | S3 replaces SeaweedFS, RDS PostgreSQL replaces in-cluster PostgreSQL, Glue replaces Polaris |

The first AWS query path still uses Trino. Athena is documented as a future
adapter because it changes query pricing, Superset wiring, and e2e validation.

To deploy the AWS or Azure POC into your own account — credentials, the
per-account `sandbox.tfvars`, and the `make` targets — see
[docs/setup/cloud-poc-setup.md](docs/setup/cloud-poc-setup.md).

## Repository Structure

```text
openlakeforge/
├── docs/
├── infra/
├── images/project-code/
├── libs/
├── domains/
│   ├── sales/
│   └── supply_chain/
├── scripts/
└── tools/olf/          # uv-managed deployment tooling (olf CLI)
```

Each domain follows this shape:

```text
domains/<domain>/
├── domain.yaml
├── contracts/floe/
├── examples/raw/<product>/
├── extract/dlt/<product>.py
├── transformations/dbt/<product>/
├── reports/superset/<product>/
└── pipelines/dagster/<product>.py
```

## Runtime Boundary

The project-code image is built as:

```text
ghcr.io/openlakeforge/project-code:<tag>
```

It contains Dagster code, `dagster-floe`, product Floe contracts, generated
product Floe manifests, dlt extract code, domain Python code, and shared
OpenLakeForge libraries. Terraform provisions the local SeaweedFS ops bucket
`openlakeforge-ops` and passes runner-facing artifact URIs to Dagster. Floe
manifests are published under `s3://openlakeforge-ops/floe/manifests`, Floe
reports under `s3://openlakeforge-ops/floe/reports`, and logs/run artifacts
under `logs/` and `run-artifacts/`.

Superset report assets are also treated as dynamic product artifacts. Their
source lives under `domains/<domain>/reports/superset/<product>/`;
local/CD deployment zips each bundle, copies it into the Superset reports
volume, and imports it into the running Superset instance.

OpenMetadata domain and data-product assets follow the same boundary. Terraform
creates OpenMetadata and the platform services it needs; source-controlled
domain, data-product, Bronze, Silver, and Gold metadata in
`domains/<domain>/domain.yaml` is deployed by the local/CD artifact phase.

## Roadmap

- Iteration 0: repository skeleton, architecture documentation, and validation automation.
- Iteration 1: local kind foundation with namespaces, SeaweedFS, Polaris, and Trino.
- Iteration 2: project-code image and Dagster deployment with Kubernetes run launcher.
- Iteration 3: Sales POC ingestion and Floe Silver materialization.
- Iteration 4: dbt-duckdb Gold models and Dagster-dbt integration.
- Iteration 5: OpenMetadata governance, catalog discovery, and OpenLineage ingestion (OL removed in Iteration 6 — see ADR 0009).
- Iteration 6: Superset reporting over Gold marts; OpenLineage integration deferred pending upstream connector fixes.
- Iteration 7: multi-product seed POC with product-owned dlt, Floe, dbt, Dagster, Superset, and OpenMetadata artifacts.

## Local Development

```sh
make check-structure
make check-contracts
make local-foundation-up
make local-prefetch
make local-up
```

The local stack runs on a kind cluster backed by your local Docker daemon. It
has been exercised on Docker Desktop and Colima. On macOS with Colima, start
Colima before running the make targets and make sure `docker ps` works from the
same shell.

### Prerequisites

Install these tools on the host:

| Tool | Why it is needed |
| --- | --- |
| Docker daemon | Builds local images and provides the container runtime used by kind. |
| kind | Creates the local Kubernetes cluster. |
| kubectl | Applies and inspects Kubernetes resources. |
| Terraform | Creates the kind foundation and platform infrastructure. |
| Helm | Installs Kubernetes applications through Terraform Helm releases. |
| Python 3 | Runs local metadata and manifest helper scripts. |
| uv | Runs the `tools/olf` deployment tooling (contracts, artifacts, REST calls). Install from https://docs.astral.sh/uv/. |
| Make | Provides the local workflow entrypoints. |

The `floe` CLI is optional locally because manifest generation falls back to the
Floe runner image. The helper scripts create local caches under `.tmp/` as
needed, including a small Python virtual environment for OpenMetadata metadata
deployment if the active host Python does not already include `PyYAML`.

For a new macOS/Colima setup, a typical tool bootstrap is:

```sh
brew install colima docker kind kubectl terraform helm python uv make
colima start --cpu 6 --memory 12 --disk 100
docker ps
```

If your network uses a corporate TLS interception proxy, Docker inside Colima
and the kind nodes must trust that CA. The early symptom is usually an image
pull failure like `x509: certificate signed by unknown authority`. Fix host or
Colima trust first, then use `make local-prefetch` so Kubernetes does not need
to pull large images during Helm or Dagster execution.

### Bring Up The Stack

Run the static validations first:

```sh
make check-structure
make check-contracts
```

Create the local kind foundation once:

```sh
make local-foundation-up
```

Preload heavy runtime images into the kind nodes. This is strongly recommended
on macOS, Colima, slow networks, and corporate networks:

```sh
make local-prefetch
```

Apply the platform and deploy dynamic artifacts:

```sh
make local-up
```

`make local-up` is the full wrapper. It runs the foundation, then the stack:

1. `make local-foundation-up` creates the kind foundation (a no-op when it
   already exists).
2. `make local-infra-up` builds and loads the local Superset image, then applies
   Terraform for SeaweedFS, PostgreSQL, Polaris, Trino, OpenMetadata, Superset,
   and Dagster.
3. `make local-artifacts-deploy` builds and loads the project-code image,
   generates and uploads Floe manifests, imports Superset reports, deploys
   OpenMetadata governance metadata, and restarts Dagster workloads.

`make local-prefetch` is still run manually — it is network-dependent and
strongly recommended before `make local-up` on macOS, Colima, and constrained
networks. Use `make local-stack-up` to run only infra + artifacts without
re-applying the foundation.

Check the cluster at any point with:

```sh
make local-status
```

### Access Local Services

Start port-forwards in a long-running terminal:

```sh
make local-forward
```

Then open:

| Service | URL | Credentials |
| --- | --- | --- |
| Dagster | http://localhost:3000 | none |
| Superset | http://localhost:8088 | `admin / admin` |
| OpenMetadata | http://localhost:8585 | `admin@open-metadata.org / admin` |
| Trino | http://localhost:8080 | none |
| Polaris API | http://localhost:8181/api/catalog | service credentials |
| SeaweedFS S3 | http://localhost:9000 | generated local secret |
| SeaweedFS Filer | http://localhost:8888 | none |
| SeaweedFS Master | http://localhost:9333 | none |

The SeaweedFS Filer UI is the simplest local bucket browser for this stack. It
uses the existing SeaweedFS deployment, so no extra component or S3 credential
setup is needed. The Master UI is useful for quick cluster and volume status.

In Dagster, launch `sales_order_revenue_pipeline`,
`sales_customer_health_pipeline`, or
`supply_chain_inventory_reliability_pipeline` to run the product
`dlt -> Floe -> dbt-duckdb` pipelines. Superset dashboards query the Gold
Iceberg marts through Trino.

### Common Local Recovery

If a Kubernetes pod cannot pull an image because of TLS or network issues, rerun:

```sh
make local-prefetch
```

If Polaris restarts while using local in-memory persistence, clients can hold
stale OAuth credentials. `make local-up` now checks Trino and refreshes it when
that happens, but for manual recovery you can restart Trino directly:

```sh
kubectl --context kind-openlakeforge-local -n lakehouse rollout restart deployment/trino-coordinator
```

To remove the local platform while keeping the kind foundation:

```sh
make local-down
```

To remove the foundation cluster as well:

```sh
make local-foundation-down
```

## AWS POC

The AWS POC is contract-compatible with local and Azure but uses EKS, ECR, S3,
RDS PostgreSQL, Glue, and EKS Pod Identity. Default region is `eu-west-1`; override
`AWS_REGION` and the related `AWS_*` Make variables as needed.

```sh
make aws-up
make aws-forward
make aws-e2e
```

`make aws-up` is the full wrapper: it runs `aws-foundation-up` (VPC, EKS, ECR,
Pod Identity), then the stack (`aws-infra-up` + `aws-artifacts-deploy`). The
foundation apply is a no-op once it exists. Use `make aws-stack-up` to redeploy
only the stack, or `make aws-foundation-up` on its own to provision the cluster
first. Azure follows the same pattern (`make azure-up` / `make azure-stack-up`).

Teardown runs in the opposite order:

```sh
make aws-down
make aws-foundation-down
```

See [docs/architecture/aws-eks-poc.md](docs/architecture/aws-eks-poc.md) for the
AWS contract shape, managed-service boundaries, and current compatibility gate.
