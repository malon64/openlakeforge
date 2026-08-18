# Local installation

This guide explains how to deploy OpenLakeForge on your machine using a local Kubernetes cluster.

The local environment is the easiest way to evaluate OpenLakeForge, develop data products, and work on the platform itself.

OpenLakeForge creates an isolated [`kind`](https://kind.sigs.k8s.io/) cluster backed by your existing Docker engine. It does **not** modify your global Kubernetes context.

## What gets deployed?

The local environment is available in two profiles:

| Profile  | Includes                                                                    | Recommended for                       |
| -------- | --------------------------------------------------------------------------- | ------------------------------------- |
| **Slim** | Ingestion, Floe validation, Apache Iceberg, Polaris, Trino, dbt and Dagster | First evaluation and data engineering |
| **Full** | Slim + OpenMetadata + Superset                                              | Governance, lineage and dashboards    |

Both profiles use the same ingestion-to-Gold data path.

The Slim profile only disables the optional governance and analytics layers:

```text
Sources
   ↓
dlt
   ↓
Bronze
   ↓
Floe
   ↓
Silver Iceberg
   ↓
dbt-trino
   ↓
Gold Iceberg
   ↓
Trino
```

Dagster orchestrates the complete pipeline.

---

## Requirements

OpenLakeForge currently runs locally from a source checkout.

You need the following tools available on your `PATH`:

| Tool             | Purpose                               |
| ---------------- | ------------------------------------- |
| Git              | Clone OpenLakeForge                   |
| Docker engine    | Container runtime used by kind        |
| kind             | Local Kubernetes cluster              |
| kubectl          | Kubernetes access and troubleshooting |
| Terraform >= 1.7 | Infrastructure provisioning           |
| Helm             | Kubernetes application deployment     |
| Python >= 3.12   | OpenLakeForge tooling                 |
| uv               | Python dependency and CLI execution   |
| Make             | OpenLakeForge workflow entry points   |

You do **not** need Docker Desktop specifically.

Any Docker-compatible engine that works with `kind` can be used.

### Verify your tools

Check that each command is available:

```bash
git --version
docker --version
kind --version
kubectl version --client
terraform version
helm version
python3 --version
uv --version
make --version
```

Verify that your Docker engine is running:

```bash
docker ps
```

If this command fails, fix your Docker installation before continuing.

---

## Clone OpenLakeForge

```bash
git clone https://github.com/malon64/openlakeforge.git
cd openlakeforge
```

OpenLakeForge commands in this guide should be executed from the repository root.

> OpenLakeForge is currently alpha. A standalone installation artifact is being developed so future releases will not require operating directly from a source checkout.

---

# Deploy the Slim profile

For a first installation, **Slim is recommended**.

It keeps the complete data-engineering path while leaving OpenMetadata and Superset out of the deployment.

Run:

```bash
make local-slim-up
```

This command performs the complete local deployment.

It will:

1. Create the local kind Kubernetes cluster.
2. Pre-fetch the large runtime images used by the platform.
3. Deploy the Slim OpenLakeForge platform.
4. Build the OpenLakeForge project-code image.
5. Load the project-code image into kind.
6. Generate and deploy runtime artifacts.
7. Register the included data products with Dagster.

The initial installation can take several minutes because Kubernetes and application container images need to be downloaded.

---

## Check the deployment

Once installation finishes, inspect the platform:

```bash
make local-status
```

This shows:

* Kubernetes pods
* services
* persistent volume claims

You can also inspect Kubernetes directly using OpenLakeForge's isolated kubeconfig:

```bash
KUBECONFIG=.tmp/kubeconfigs/local.yaml \
kubectl --context kind-openlakeforge-local get pods -n lakehouse
```

The local deployment uses:

```text
Cluster:    openlakeforge-local
Context:    kind-openlakeforge-local
Namespace:  lakehouse
Kubeconfig: .tmp/kubeconfigs/local.yaml
```

The generated kubeconfig is intentionally separate from your normal `~/.kube/config`.

---

# Validate the platform

OpenLakeForge includes example data products that can be executed end to end.

For the Slim profile, run:

```bash
make local-slim-e2e
```

The validation executes the data pipelines and verifies the core path:

```text
Bronze
  ↓
Silver Iceberg
  ↓
Gold Iceberg
  ↓
Trino query
```

Assertions related to OpenMetadata and Superset are skipped because those layers are disabled in Slim.

A successful end-to-end run is the best way to confirm that your installation is working correctly.

---

# Access OpenLakeForge

OpenLakeForge services are not exposed outside Kubernetes by default.

Start local port forwarding with:

```bash
make local-forward
```

Keep this command running in its terminal.

Press `Ctrl+C` when you want to stop the port forwards.

## Core services

### Dagster

```text
http://localhost:3000
```

Dagster is the main interface for inspecting and running OpenLakeForge data pipelines.

From the Dagster UI you can:

* inspect the asset graph
* view domains and data products
* launch pipelines
* inspect individual materializations
* inspect execution logs

### Trino

```text
http://localhost:8080
```

Trino is the query engine used by OpenLakeForge and by dbt to build Gold models.

### Apache Polaris

```text
http://localhost:8181
```

Polaris provides the local Iceberg catalog.

### SeaweedFS

Local object storage is provided by SeaweedFS.

| Interface | Address                 |
| --------- | ----------------------- |
| S3 API    | `http://localhost:9000` |
| Filer UI  | `http://localhost:8888` |
| Master UI | `http://localhost:9333` |

The Filer UI is useful for inspecting the local buckets and data written by OpenLakeForge.

---

# Run the Full profile

The Full profile adds:

* **OpenMetadata** for governance, catalog discovery and lineage
* **Superset** for analytics dashboards

If you currently have a Slim deployment, tear it down first:

```bash
make local-slim-down
```

Then deploy Full:

```bash
make local-up
```

Validate it:

```bash
make local-e2e
```

And start the port forwards:

```bash
make local-forward
```

The additional services are then available at:

| Service      | URL                     | Development credentials           |
| ------------ | ----------------------- | --------------------------------- |
| OpenMetadata | `http://localhost:8585` | `admin@open-metadata.org / admin` |
| Superset     | `http://localhost:8088` | `admin / admin`                   |

These credentials are intended for the local development environment only.

---

# What `local-up` actually does

The local deployment is divided into three logical phases.

```text
Foundation
    ↓
Platform
    ↓
Artifacts
```

The top-level command:

```bash
make local-up
```

is equivalent to running:

```bash
make local-foundation-up
make local-prefetch
make local-platform-up
make local-artifacts-deploy
```

For Slim, the equivalent wrapper is:

```bash
make local-slim-up
```

Understanding these phases is useful when developing or troubleshooting OpenLakeForge.

---

## 1. Foundation

```bash
make local-foundation-up
```

The foundation creates the local Kubernetes environment.

The default kind topology contains:

```text
1 control-plane
2 workers
```

Terraform manages the cluster foundation and writes its kubeconfig to:

```text
.tmp/kubeconfigs/local.yaml
```

Running the command again is safe: Terraform reconciles the existing foundation rather than requiring you to recreate it manually.

---

## 2. Platform

Full:

```bash
make local-platform-up
```

Slim:

```bash
make local-slim-platform-up
```

This phase uses Terraform and Helm to deploy the long-lived OpenLakeForge platform services.

Depending on the selected profile, these include:

* SeaweedFS
* PostgreSQL
* Apache Polaris
* Trino
* Dagster
* OpenMetadata
* Superset

Slim disables the last two.

The platform phase only manages the relatively static infrastructure and platform services.

---

## 3. Artifacts

Full:

```bash
make local-artifacts-deploy
```

Slim:

```bash
make local-slim-artifacts-deploy
```

The artifact phase deploys the parts of OpenLakeForge that change with data-product code.

This includes operations such as:

* building the project-code image
* loading it into kind
* discovering domain descriptors
* reconciling catalog namespaces
* generating Floe manifests
* publishing runtime artifacts
* deploying Dagster data-product code
* deploying OpenMetadata metadata when enabled
* importing Superset reports when enabled

Keeping platform infrastructure separate from data-product artifacts allows product changes to be deployed without rebuilding the entire environment.

See [Architecture](../architecture/README.md) for the deeper deployment model.

---

# Common workflows

## Check platform status

```bash
make local-status
```

## Refresh data-product artifacts

If you changed domain code, contracts, pipelines or dbt models without changing the infrastructure:

```bash
make local-artifacts-deploy
```

For Slim:

```bash
make local-slim-artifacts-deploy
```

## Reapply platform infrastructure

```bash
make local-platform-up
```

This is useful after changing Terraform or Helm configuration.

## Run the complete validation suite

Full:

```bash
make local-e2e
```

Slim:

```bash
make local-slim-e2e
```

## Run the Slim smoke test

```bash
make local-slim-smoke
```

The smoke test creates the Slim environment and validates one discovered data product through to a queryable Gold table.

It is intended as a bounded deployment validation rather than a replacement for the full end-to-end suite.

---

# Local configuration

The most commonly configurable values are exposed through Make variables.

Default local configuration:

```text
Cluster name: openlakeforge-local
Namespace:    lakehouse
Kube context: kind-openlakeforge-local
Kubeconfig:   .tmp/kubeconfigs/local.yaml
```

For example, to use another cluster name:

```bash
make local-up CLUSTER_NAME=my-openlakeforge
```

Or another namespace:

```bash
make local-up NAMESPACE=my-lakehouse
```

The local kubeconfig path can also be overridden:

```bash
make local-up LOCAL_KUBECONFIG_PATH=/path/to/openlakeforge-kubeconfig.yaml
```

For normal evaluation, the defaults are recommended.

---

# Troubleshooting

## Docker is unavailable

Check:

```bash
docker ps
```

If this fails, OpenLakeForge cannot create the kind cluster or build local images.

Make sure your Docker-compatible engine is running and accessible from the same shell in which you run `make`.

---

## A required command is missing

The deployment scripts fail early when required tools are not available on `PATH`.

For example:

```text
ERROR: 'terraform' not found on PATH
```

Install the missing tool and run the command again.

---

## Kubernetes cluster is unreachable

Check the local kubeconfig:

```bash
KUBECONFIG=.tmp/kubeconfigs/local.yaml \
kubectl --context kind-openlakeforge-local cluster-info
```

If the cluster does not exist, recreate the foundation:

```bash
make local-foundation-up
```

Then continue with:

```bash
make local-platform-up
make local-artifacts-deploy
```

Or simply rerun:

```bash
make local-up
```

---

## A pod is not starting

First inspect the platform:

```bash
make local-status
```

Then inspect the failing pod:

```bash
KUBECONFIG=.tmp/kubeconfigs/local.yaml \
kubectl --context kind-openlakeforge-local \
describe pod <pod-name> -n lakehouse
```

Check its logs:

```bash
KUBECONFIG=.tmp/kubeconfigs/local.yaml \
kubectl --context kind-openlakeforge-local \
logs <pod-name> -n lakehouse
```

If the pod contains multiple containers:

```bash
KUBECONFIG=.tmp/kubeconfigs/local.yaml \
kubectl --context kind-openlakeforge-local \
logs <pod-name> -c <container-name> -n lakehouse
```

---

## Image pulls are slow or timing out

OpenLakeForge provides a local prefetch step:

```bash
make local-prefetch
```

This downloads larger runtime images ahead of Helm deployment and loads them into the kind nodes.

`make local-up` and `make local-slim-up` already execute this automatically.

It can still be useful to rerun it independently after a failed image pull.

---

## Corporate proxy or custom TLS certificates

If your network intercepts HTTPS traffic, your Docker engine and kind nodes may reject external registries with an error similar to:

```text
x509: certificate signed by unknown authority
```

This is a container-runtime trust issue rather than an OpenLakeForge issue.

Configure your Docker engine to trust your organization's CA certificate, verify that images can be pulled normally, then retry:

```bash
make local-prefetch
make local-up
```

The exact certificate configuration depends on the Docker engine used on your machine.

---

## A local port is already in use

`make local-forward` uses the following local ports:

| Port | Service          |
| ---: | ---------------- |
| 3000 | Dagster          |
| 8080 | Trino            |
| 8088 | Superset         |
| 8181 | Polaris          |
| 8585 | OpenMetadata     |
| 8888 | SeaweedFS Filer  |
| 9000 | SeaweedFS S3     |
| 9333 | SeaweedFS Master |

Check which process currently owns a port and stop it before running the forwarding command again.

---

## Terraform or Kubernetes state has become inconsistent

The local deployment includes recovery logic for some common state-drift situations.

First try rerunning:

```bash
make local-up
```

Terraform will reconcile resources where possible.

If you want to recreate only the platform while keeping the kind cluster:

```bash
make local-platform-down
make local-platform-up
make local-artifacts-deploy
```

For a completely clean environment, use the full teardown described below.

---

# Tear down OpenLakeForge

## Remove Slim

```bash
make local-slim-down
```

## Remove Full

```bash
make local-down
```

The full teardown removes:

1. OpenLakeForge platform services.
2. The local kind foundation.

If you only want to remove platform services while keeping the Kubernetes cluster:

```bash
make local-platform-down
```

You can later reinstall them with:

```bash
make local-platform-up
make local-artifacts-deploy
```

---

# Next steps

Once the local platform is running, the next step is to replace the included examples with one of your own data products.

➡️ [Build your first data product](../getting-started/first-data-product.md)

You may also want to read:

* [Architecture overview](../architecture/overview.md)
* [Provider contracts](../architecture/provider-contracts.md)
* [`olf` CLI documentation](../../tools/olf/README.md)
* [Architecture Decision Records](../adr/README.md)
* [AWS and Azure deployment](cloud-poc-setup.md)
