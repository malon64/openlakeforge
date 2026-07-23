# OpenLakeForge

OpenLakeForge is a cloud-agnostic, open-source, self-hostable modern lakehouse
platform. It assembles open-source data platform components on Kubernetes with
Terraform and Helm.

![OpenLakeForge Architecture](/docs/assets/openlakeforge_v1.png)

For the engineering detail behind this picture — the pod-by-pod cluster census, the
ephemeral Kubernetes job model, the Terraform contract flow, and provider portability
across kind/AKS/EKS — see the
[architecture charts](docs/architecture/diagrams/README.md).

The v1 proof of concept focuses on a local Kubernetes lakehouse path across
multiple domain-owned data products:

```text
CSV examples
  -> Bronze landing
  -> Floe validation
  -> Silver Iceberg tables through Polaris
  -> dbt-trino Gold marts
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
| Transformation | dbt-trino | Silver-to-Gold business models |
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
local/CD deployment zips each bundle, copies it into Superset's ephemeral report
staging directory, and imports it into the running Superset instance.

OpenMetadata domain and data-product assets follow the same boundary. Terraform
creates OpenMetadata and the platform services it needs; source-controlled
domain, data-product, Bronze, Silver, and Gold metadata in
`domains/<domain>/domain.yaml` is deployed by the local/CD artifact phase.

## Roadmap

The proposed path from the current POC to a supportable distribution is defined
in the [OpenLakeForge Industrialization Roadmap](docs/industrialization-roadmap.md).
The GitHub project remains the execution view after the proposal is approved.

The iterations below record the POC delivery history:

- Iteration 0: repository skeleton, architecture documentation, and validation automation.
- Iteration 1: local kind foundation with namespaces, SeaweedFS, Polaris, and Trino.
- Iteration 2: project-code image and Dagster deployment with Kubernetes run launcher.
- Iteration 3: Sales POC ingestion and Floe Silver materialization.
- Iteration 4: dbt-trino Gold models and Dagster-dbt integration.
- Iteration 5: OpenMetadata governance, catalog discovery, and OpenLineage ingestion (OL removed in Iteration 6 — see ADR 0009).
- Iteration 6: Superset reporting over Gold marts; OpenLineage integration deferred pending upstream connector fixes.
- Iteration 7: multi-product seed POC with product-owned dlt, Floe, dbt, Dagster, Superset, and OpenMetadata artifacts.

## Local Development

```sh
make check-structure
make check-contracts
make local-up
```

The local workflow uses `.tmp/kubeconfigs/local.yaml` and never changes your
global kubeconfig context. Set `LOCAL_KUBECONFIG_PATH` to override that path.

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

The `floe` CLI is optional locally because OpenLakeForge can generate manifests
through a Dockerized Floe CLI. Runtime execution does not depend on a host
installation: Dagster launches the manifest-declared Floe runner image in
Kubernetes. The helper scripts create local caches under `.tmp/` as needed,
including a small Python virtual environment for OpenMetadata metadata
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

Create the local kind foundation once, or let `make local-up` do it:

```sh
make local-foundation-up
```

Preload heavy runtime images into the kind nodes when you want to do it
explicitly. `make local-up` also runs this step after the foundation exists:

```sh
make local-prefetch
```

Apply the full local stack:

```sh
make local-up
```

`make local-up` is the full wrapper:

1. `make local-foundation-up` creates the kind foundation (a no-op when it
   already exists).
2. `make local-prefetch` pre-pulls heavy images into the kind nodes.
3. `make local-platform-up` builds and loads the local Superset image, then applies
   Terraform for SeaweedFS, PostgreSQL, Polaris, Trino, OpenMetadata, Superset,
   and Dagster.
4. `make local-artifacts-deploy` builds and loads the project-code image,
   generates and uploads Floe manifests, imports Superset reports, deploys
   OpenMetadata governance metadata, and restarts Dagster workloads.

Use `make local-platform-up` or `make local-artifacts-deploy` directly when you
only need to refresh one phase.

Check the cluster at any point with:

```sh
make local-status
```

Run the local end-to-end suite after `make local-up`:

```sh
make local-e2e
```

This launches the three product Dagster pipelines, verifies Silver and Gold
tables through Trino, checks Superset dashboards, checks OpenMetadata domains
and data products, and confirms ops-bucket artifacts and logs exist.

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
`dlt -> Floe -> dbt-trino` pipelines. Superset dashboards query the Gold
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
make local-platform-down
```

To remove the platform and foundation cluster:

```sh
make local-down
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
Pod Identity), `aws-platform-up`, and `aws-artifacts-deploy`. The foundation
apply is a no-op once it exists. Azure follows the same three-step pattern with
`azure-foundation-up`, `azure-platform-up`, and `azure-artifacts-deploy`.
`make aws-e2e` runs the full shared suite through `olf e2e run --env aws`: AWS
provider, S3, Glue, and Trino preflight checks followed by Dagster product jobs,
table and mart assertions, Superset dashboards, OpenMetadata assets, and runtime
artifacts. Use `olf e2e run --env aws --suite smoke` for preflight-only checks.

Teardown runs in the opposite order:

```sh
make aws-down
```

Use `make aws-platform-down` when you want to remove only the platform while
leaving EKS, ECR, and networking in place.

See [docs/architecture/aws-eks-poc.md](docs/architecture/aws-eks-poc.md) for the
AWS contract shape, managed-service boundaries, and current compatibility gate.
