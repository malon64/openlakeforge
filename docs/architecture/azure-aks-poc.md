# Azure AKS POC

The first Azure target is an AKS-based proof of concept, not an Azure-native
platform rewrite. It keeps the existing in-cluster OpenLakeForge services:
SeaweedFS, Polaris, PostgreSQL, Trino, Dagster, OpenMetadata, and Superset.
Only the local-kind mechanics are replaced:

- AKS provides Kubernetes.
- ACR distributes the Superset and project-code images.
- Access remains `kubectl port-forward`; there is no public ingress, DNS, TLS,
  Keycloak, or Vault.

This POC is not production hardened. It still uses in-cluster storage,
databases, generated Terraform state credentials, and Kubernetes Secrets. Future
Azure phases can replace those pieces with Azure Blob or ADLS Gen2, Azure
PostgreSQL Flexible Server, Key Vault with External Secrets, managed identity,
private networking, and managed observability.

## Prerequisites

The shell running the Azure workflow needs:

- Azure CLI logged in with `az login`.
- The intended subscription selected with `az account set`.
- Permissions to create resource groups, AKS clusters, ACR registries, and role
  assignments.
- Terraform, Helm, kubectl, Docker, and Python 3.
- Docker access to build local images before pushing to ACR.

Defaults:

- Resource group: `rg-openlakeforge-azure-poc`
- Region: `westeurope`
- AKS cluster: `aks-openlakeforge-poc`
- Node pool: 3 `Standard_D4s_v5` nodes
- ACR name: `openlakeforgepoc<random_suffix>`

By default, the Azure foundation Terraform root owns the resource group. That
is the normal path for a subscription where the operator can create resource
groups:

```bash
make azure-foundation-up
```

Some corporate sandboxes only allow resource creation inside a pre-created
resource group. In that restricted case, keep the resource group outside the
foundation state and set `AZURE_CREATE_RESOURCE_GROUP=false`. Terraform will
read the existing resource group and still create the POC-owned resources inside
it: AKS, ACR, and the AKS-to-ACR pull role assignment.

```bash
AZURE_RESOURCE_GROUP=<existing-resource-group> \
AZURE_CREATE_RESOURCE_GROUP=false \
AZURE_LOCATION=<resource-group-region> \
make azure-foundation-up
```

Use the same overrides for every Azure lifecycle command in that sandbox,
including `azure-up`, `azure-down`, and `azure-foundation-down`. With
`AZURE_CREATE_RESOURCE_GROUP=false`, foundation destroy removes the AKS and ACR
resources owned by this stack but leaves the external resource group in place.

## Workflow

Create AKS and ACR:

```bash
make azure-foundation-up
```

Deploy the platform:

```bash
make azure-up
```

`make azure-up` runs two phases:

1. `make azure-infra-up` builds and pushes the Superset image to ACR, then runs
   Terraform in `infra/terraform/environments/azure-poc`. Superset is pushed
   before Terraform because the Helm release waits for Superset pods.
2. `make azure-artifacts-deploy` generates Floe manifests, builds and pushes
   the project-code image to ACR, uploads Floe manifests through the SeaweedFS
   S3-compatible API via port-forward, imports Superset report bundles, deploys
   OpenMetadata metadata, and restarts Dagster deployments.

Runtime images are pushed as:

```text
<acr_login_server>/openlakeforge/project-code:<tag>
<acr_login_server>/openlakeforge/superset:<tag>
```

The default tag is `azure-<git_sha>`, falling back to a UTC timestamp when the
repository is unavailable.

Forward service UIs to localhost:

```bash
make azure-forward
```

Ports match the local stack:

- Dagster: `http://localhost:3000`
- Superset: `http://localhost:8088`
- OpenMetadata: `http://localhost:8585`
- Trino: `http://localhost:8080`
- Polaris: `http://localhost:8181`
- SeaweedFS S3: `http://localhost:9000`

## Validation

Static checks include the Azure roots and contracts:

```bash
make check-structure
make check-contracts
make check-infra
```

After `make azure-up`, run:

```bash
make azure-e2e
```

The e2e check:

- Confirms pods are `Running` or completed bootstrap pods are `Succeeded`.
- Launches and polls these Dagster jobs to `SUCCESS`:
  `sales_order_revenue_pipeline`, `sales_customer_health_pipeline`, and
  `supply_chain_inventory_reliability_pipeline`.
- Confirms Trino exposes the `iceberg` catalog.
- Confirms Silver has 15 tables and Gold has 9 marts.
- Confirms each Gold mart has at least one row.
- Confirms the three Superset dashboard slugs are imported.
- Confirms OpenMetadata contains the `sales` and `supply_chain` domains and the
  three data products.

## Teardown

Destroy the platform first:

```bash
make azure-down
```

Then destroy AKS, ACR, and the resource group resources:

```bash
make azure-foundation-down
```

The foundation destroy target refuses to run while the `lakehouse` namespace
exists unless `AZURE_FOUNDATION_FORCE_DOWN=true` is set.
