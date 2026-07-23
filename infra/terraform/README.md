# Terraform

Terraform will own environment assembly and reusable platform modules.

Implemented structure:

```text
infra/terraform/
├── foundations/
│   ├── azure-aks/
│   ├── aws-eks/
│   └── local-kind/
├── environments/
│   ├── azure-poc/
│   ├── aws-poc/
│   └── local/
└── modules/
    ├── storage/aws-s3/
    ├── storage/rds-postgresql/
    ├── storage/seaweedfs/
    ├── storage/postgresql/
    ├── catalog/aws-glue/
    ├── catalog/polaris/
    ├── query/trino/
    ├── analytics/superset/
    ├── orchestration/dagster/
    └── governance/openmetadata/
```

The local foundation root creates the kind cluster. The Azure foundation root
creates the AKS cluster and ACR registry. The AWS foundation root creates the
VPC, EKS cluster, node group, ECR registries, and EKS Pod Identity readiness.
Environment roots then deploy OpenLakeForge into the selected cluster through
the Kubernetes and Helm providers. Static, non-secret Helm chart values live in
`../helm/values/local`; Terraform modules overlay the dynamic contract values
and Secret references. Azure reuses the in-cluster service implementations while
AWS swaps storage, metadata PostgreSQL, and catalog dependencies for managed
services.

Each environment root normalizes its provider contracts in `contracts.tf`.
Those typed contract objects are the source of truth for storage, catalog,
metadata database, artifacts, secrets, identity, access, observability, query,
orchestration, reporting, and governance boundaries.

## Local workflow

```bash
make local-foundation-up
make local-up
make local-down
make local-foundation-down
```

`make local-foundation-up` runs `terraform init` and `terraform apply` in
`infra/terraform/foundations/local-kind`. Terraform owns the local kind cluster
lifecycle while the cluster definition remains in `infra/kind/local`.

`make local-up` runs two platform phases:

```bash
make local-platform-up
make local-artifacts-deploy
```

`make local-platform-up` runs `terraform init` and a normal `terraform apply` in
`infra/terraform/environments/local`. Terraform owns:

- Kubernetes namespace creation
- SeaweedFS, Polaris, Trino, and Superset Helm releases
- Dagster Helm release
- dynamic Helm values passed to those releases
- local generated credentials
- Kubernetes Secrets used as service contracts
- SeaweedFS bucket creation jobs
- Polaris catalog and Trino principal bootstrap jobs
- Polaris Floe principal bootstrap credentials for manifest-driven Floe jobs
- shared local PostgreSQL for Dagster, OpenMetadata, and Superset metadata
- Dagster webserver, daemon, domain product code servers, and Kubernetes run launcher
- Superset webserver, worker, ephemeral report staging volume, and local report deploy path
- OpenMetadata, Polaris service metadata, and catalog ingestion plumbing
- SeaweedFS S3, Filer, and Master services for local object storage and inspection

`make local-artifacts-deploy` owns the local/CD artifacts:

- project-code image build/load
- product Floe manifest generation and upload to the local ops bucket
- product Superset report import
- OpenMetadata domain, data-product, Bronze, Silver, and Gold metadata from domain YAML files
- Dagster rollout after dynamic artifacts are available

Terraform state is local and contains generated development credentials. Treat
state files as sensitive; they are gitignored.

No remote state backend, Keycloak, Vault, ingress/TLS, Lake Formation, or cloud
secret manager integration is implemented yet.

## Azure AKS POC workflow

```bash
make azure-foundation-up
make azure-up
make azure-e2e
make azure-down
make azure-foundation-down
```

`make azure-foundation-up` runs Terraform in
`infra/terraform/foundations/azure-aks` to create the AKS cluster, ACR registry,
AKS-to-ACR `AcrPull` role assignment, and AKS OIDC / Workload Identity
readiness. The wrapper then runs `az aks get-credentials`.

Resource-group settings are required in a local, gitignored tfvars file. Copy
the template and configure the group supplied by your sandbox:

```bash
cd infra/terraform/foundations/azure-aks
cp sandbox.tfvars.example sandbox.tfvars
# Edit resource_group_name, create_resource_group, location, and node_vm_size.
cd ../../../..
make azure-foundation-up
```

With `create_resource_group = false`, Terraform reads the resource group as
data, creates AKS and ACR inside it, and leaves the group untouched during
foundation destroy. Set `AZURE_TFVARS_FILE` to use a file outside the default
foundation directory. With `create_resource_group = true`, Terraform creates
the group and uses `rg-openlakeforge-azure-poc` when `resource_group_name` is
omitted.

`make azure-up` runs:

```bash
make azure-platform-up
make azure-artifacts-deploy
```

`make azure-platform-up` builds and pushes the custom Superset image to ACR before
Terraform apply because the Superset Helm release waits for pods during install.
`make azure-artifacts-deploy` generates Floe manifests, builds and pushes the
project-code image, uploads manifests to the in-cluster SeaweedFS ops bucket,
imports Superset reports, deploys OpenMetadata metadata, and restarts Dagster.

The Azure POC keeps SeaweedFS, PostgreSQL, Polaris, and Kubernetes Secrets
in-cluster. Azure Blob/ADLS, Azure PostgreSQL Flexible Server, Key Vault,
managed identity-backed runtime auth, ingress, DNS, and TLS are future phases,
not active POC implementations.

## AWS EKS POC workflow

```bash
make aws-foundation-up
make aws-up
make aws-e2e
make aws-down
make aws-foundation-down
```

`make aws-foundation-up` runs Terraform in
`infra/terraform/foundations/aws-eks` to create the VPC, EKS cluster, managed
node group, EKS add-ons, ECR repositories, and EKS Pod Identity add-on/roles.
The wrapper then runs `aws eks update-kubeconfig`.

`make aws-up` runs:

```bash
make aws-platform-up
make aws-artifacts-deploy
```

`make aws-platform-up` builds and pushes the custom Superset image to ECR before
Terraform apply. The AWS platform root creates S3 medallion and ops buckets,
RDS PostgreSQL, product-layer Glue databases/namespaces, Pod Identity workload
access, and the shared Helm services on EKS.

`make aws-artifacts-deploy` generates Floe manifests, builds and pushes the
project-code image, uploads manifests directly to the S3 ops bucket, imports
Superset reports, deploys OpenMetadata metadata, patches Dagster images, and
restarts Dagster workloads.

The AWS POC keeps Trino as the query path. Athena, Secrets Manager, External
Secrets, Lake Formation, ingress, DNS, TLS, and remote state are future
adapters.
