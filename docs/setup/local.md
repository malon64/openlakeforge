# Local installation

This guide explains how to deploy OpenLakeForge on your machine using a local Kubernetes cluster.

The local environment is the easiest way to evaluate OpenLakeForge, develop data products, and work on the platform itself.

OpenLakeForge creates an isolated [`kind`](https://kind.sigs.k8s.io/) cluster backed by your existing Docker engine. It does **not** modify your global Kubernetes context.

## What gets deployed?

The local environment is available in two profiles:

| Profile  | Includes                                                                    | Recommended for                       |
| -------- | ---------------------------------------------------------------------------- | ------------------------------------- |
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

You need a Docker-compatible engine and Python 3.12 or later. Git, uv, Make,
Terraform, Helm, kubectl, and kind are not host prerequisites.

Make sure Docker is available from your shell:

```bash
docker ps
```

If this command fails, fix your Docker installation before continuing.

---

## Install and deploy

Create a project directory, install the release, and initialize its writable user code:

```bash
mkdir my-lakehouse
cd my-lakehouse

pip install openlakeforge
olf init
```

`olf init` verifies the packaged platform payload, installs or reuses the
release-pinned Terraform, Helm, kubectl, and kind toolchain under
`~/.openlakeforge`, checks Docker, and creates a writable `lakehouse_code/`
directory copied from the demo. Set `OLF_TOOLCHAIN_MODE=host` to use your own
host-installed Terraform, Helm, kubectl, and kind instead of the managed
toolchain.

`olf init --empty` creates a transitional project with no source, domain, or
product. Scaffold its first source and product before deploying:

```bash
olf source new crm --resource accounts
olf product new sales/accounts_report --input crm/accounts --gold-table mart_accounts
```

The empty descriptor deliberately does not pass strict descriptor validation
until that first product exists.

---

# Deploy the Slim profile

For a first installation, **Slim is recommended**.

It keeps the complete data-engineering path while leaving OpenMetadata and Superset out of the deployment.

Run:

```bash
olf deploy --provider local --profile slim
```

This command performs the complete local deployment. It will:

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
olf status --provider local
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
olf e2e run --env local --suite full
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
olf forward --provider local --profile slim
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
olf destroy --provider local
```

Then deploy Full:

```bash
olf deploy --provider local --profile full
```

Validate it:

```bash
olf e2e run --env local --suite full
```

And start the port forwards:

```bash
olf forward --provider local --profile full
```

The additional services are then available at:

| Service      | URL                     | Development credentials           |
| ------------ | ----------------------- | --------------------------------- |
| OpenMetadata | `http://localhost:8585` | `admin@open-metadata.org / admin` |
| Superset     | `http://localhost:8088` | `admin / admin`                   |

These credentials are intended for the local development environment only.

---

# What `olf deploy` actually does

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
olf deploy --provider local
```

is equivalent to running each phase in order with `--phase`:

```bash
olf deploy --provider local --phase foundation
olf deploy --provider local --phase prefetch
olf deploy --provider local --phase platform
olf deploy --provider local --phase artifacts
```

Understanding these phases is useful when developing or troubleshooting OpenLakeForge.

---

## 1. Foundation

```bash
olf deploy --provider local --phase foundation
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

```bash
olf deploy --provider local --profile full --phase platform
```

or, for Slim:

```bash
olf deploy --provider local --profile slim --phase platform
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

```bash
olf deploy --provider local --profile full --phase artifacts
```

or, for Slim:

```bash
olf deploy --provider local --profile slim --phase artifacts
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
olf status --provider local
```

## Refresh data-product artifacts

If you changed domain code, contracts, pipelines or dbt models without changing the infrastructure:

```bash
olf deploy --provider local --profile full --phase artifacts
```

For Slim:

```bash
olf deploy --provider local --profile slim --phase artifacts
```

## Reapply platform infrastructure

```bash
olf deploy --provider local --profile full --phase platform
```

For Slim:

```bash
olf deploy --provider local --profile slim --phase platform
```

This is useful after changing Terraform or Helm configuration. Match the
profile to what you actually deployed — the default is `full`, and applying
it against a Slim platform adds OpenMetadata and Superset instead of
reconciling the stack you have.

## Run the complete validation suite

```bash
olf e2e run --env local --suite full
```

This works for both Slim and Full: assertions for a disabled layer (OpenMetadata, Superset) are skipped rather than failed.

## Run the Slim smoke test

```bash
uv run --project tools/olf --locked olf smoke run
```

The smoke test creates the Slim environment and validates one discovered data product through to a queryable Gold table.

It is intended as a bounded deployment validation rather than a replacement for the full end-to-end suite. It is currently a contributor-checkout command (see below).

---

# Local configuration

The most commonly configurable values are exposed as `olf` options.

Default local configuration:

```text
Cluster name: openlakeforge-local
Namespace:    lakehouse
Kube context: kind-openlakeforge-local
Kubeconfig:   .tmp/kubeconfigs/local.yaml
```

For example, to use another cluster name:

```bash
olf deploy --provider local --cluster-name my-openlakeforge
```

Or another namespace:

```bash
olf deploy --provider local --namespace my-lakehouse
```

The local kubeconfig path can also be overridden:

```bash
olf deploy --provider local --kubeconfig-path /path/to/openlakeforge-kubeconfig.yaml
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

Make sure your Docker-compatible engine is running and accessible from the same shell in which you run `olf`.

## A required command is missing

`olf` provisions its own managed Terraform, Helm, kubectl, and kind, so this
usually means Docker or Python (the actual host prerequisites) is missing:

```text
ERROR: 'docker' not found on PATH
```

Install the missing tool and run the command again.

If `olf` itself reports it cannot provision a managed tool, run `olf doctor`
for the actionable reason:

```bash
olf doctor --provider local --profile slim
```

If you set `OLF_TOOLCHAIN_MODE=host` to use your own host-installed
Terraform, Helm, kubectl, or kind, make sure that tool is on `PATH`.

## Kubernetes cluster is unreachable

Check the local kubeconfig:

```bash
KUBECONFIG=.tmp/kubeconfigs/local.yaml \
kubectl --context kind-openlakeforge-local cluster-info
```

If the cluster does not exist, recreate the foundation:

```bash
olf deploy --provider local --phase foundation
```

Then continue with (matching whichever profile you deployed):

```bash
olf deploy --provider local --profile full --phase platform     # or --profile slim
olf deploy --provider local --profile full --phase artifacts    # or --profile slim
```

Or simply rerun:

```bash
olf deploy --provider local
```

## A pod is not starting

First inspect the platform:

```bash
olf status --provider local
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

## Image pulls are slow or timing out

OpenLakeForge provides a local prefetch step:

```bash
olf deploy --provider local --phase prefetch
```

This downloads larger runtime images ahead of Helm deployment and loads them into the kind nodes.

`olf deploy --provider local` already executes this automatically.

It can still be useful to rerun it independently after a failed image pull.

## Corporate proxy or custom TLS certificates

If your network intercepts HTTPS traffic, your Docker engine and kind nodes may reject external registries with an error similar to:

```text
x509: certificate signed by unknown authority
```

This is a container-runtime trust issue rather than an OpenLakeForge issue.

Configure your Docker engine to trust your organization's CA certificate, verify that images can be pulled normally, then retry:

```bash
olf deploy --provider local --phase prefetch
olf deploy --provider local
```

The exact certificate configuration depends on the Docker engine used on your machine.

## A local port is already in use

`olf forward` uses the following local ports:

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

## Terraform or Kubernetes state has become inconsistent

The local deployment includes recovery logic for some common state-drift situations.

First try rerunning:

```bash
olf deploy --provider local
```

Terraform will reconcile resources where possible.

If you want to recreate only the platform while keeping the kind cluster
(matching whichever profile you deployed):

```bash
olf destroy --provider local --phase platform
olf deploy --provider local --profile full --phase platform     # or --profile slim
olf deploy --provider local --profile full --phase artifacts    # or --profile slim
```

For a completely clean environment, use the full teardown described below.

---

# Tear down OpenLakeForge

## Remove the platform

```bash
olf destroy --provider local
```

The default teardown removes:

1. OpenLakeForge platform services.
2. The local kind foundation.

If you only want to remove platform services while keeping the Kubernetes cluster:

```bash
olf destroy --provider local --phase platform
```

You can later reinstall them with (matching whichever profile you had):

```bash
olf deploy --provider local --profile full --phase platform     # or --profile slim
olf deploy --provider local --profile full --phase artifacts    # or --profile slim
```

---

# Contributor checkout

Contributors working from a source checkout of the repository use the same
`olf` commands above, run through `uv`:

```bash
uv run --project tools/olf --locked olf deploy --provider local --profile slim
```

That workflow additionally needs Git and uv on `PATH`:

| Tool           | Purpose                              |
| -------------- | ------------------------------------- |
| Git            | Clone OpenLakeForge                   |
| uv             | Python dependency and CLI execution   |

`Make` is optional, deprecated compatibility — its targets are one-line
delegates to the same `uv run ... olf` commands and are never required to
run them directly.

Clone the repository and run commands from its root:

```bash
git clone https://github.com/malon64/openlakeforge.git
cd openlakeforge
```

`Make` targets such as `make local-slim-up` remain as thin, deprecated
delegates to the exact `olf` commands documented above — see
[AGENTS.md](../../AGENTS.md) for the full contributor workflow and gates.

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
