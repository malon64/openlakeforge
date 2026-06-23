# Terraform

Terraform will own environment assembly and reusable platform modules.

Implemented structure:

```text
infra/terraform/
├── foundations/
│   ├── azure-aks/
│   └── local-kind/
├── environments/
│   ├── azure-poc/
│   └── local/
└── modules/
    ├── storage/seaweedfs/
    ├── storage/postgresql/
    ├── catalog/polaris/
    ├── query/trino/
    ├── analytics/superset/
    ├── orchestration/dagster/
    └── governance/openmetadata/
```

The local foundation root creates the kind cluster. The Azure foundation root
creates the AKS cluster and ACR registry. Environment roots then deploy
OpenLakeForge into the selected cluster through the Kubernetes and Helm
providers. Static, non-secret Helm chart values live in `../helm/values/local`;
Terraform modules overlay the dynamic contract values and Secret references.
The Azure POC intentionally reuses those values and modules while emitting
Azure-specific provider contracts.

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
make local-infra-up
make local-artifacts-deploy
```

`make local-infra-up` runs `terraform init` and a normal `terraform apply` in
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
- Superset webserver, worker, reports volume, and local report deploy path
- OpenMetadata, Polaris service metadata, and catalog ingestion plumbing

`make local-artifacts-deploy` owns the local/CD artifacts:

- project-code image build/load
- product Floe manifest generation and upload to the local ops bucket
- product Superset report import
- OpenMetadata domain, data-product, Bronze, Silver, and Gold metadata from domain YAML files
- Dagster rollout after dynamic artifacts are available

Terraform state is local and contains generated development credentials. Treat
state files as sensitive; they are gitignored.

No AWS environment, AWS provider blocks, remote state backend, Keycloak, Vault,
or cloud secret manager integration is implemented yet.

## Azure AKS POC workflow

```bash
make azure-foundation-up
make azure-up
make azure-e2e
make azure-down
make azure-foundation-down
```

`make azure-foundation-up` runs Terraform in
`infra/terraform/foundations/azure-aks` to create the resource group, AKS
cluster, ACR registry, AKS-to-ACR `AcrPull` role assignment, and AKS OIDC /
Workload Identity readiness. The wrapper then runs `az aks get-credentials`.

The default Azure foundation model owns the resource group because that keeps
the POC lifecycle self-contained: `make azure-foundation-down` can remove the
whole foundation after `make azure-down` removes the in-cluster platform.
Restricted corporate sandboxes may provide a resource group and only allow
resource creation inside that scope. For that case, run the same foundation with
an externally managed resource group:

```bash
AZURE_RESOURCE_GROUP=<existing-resource-group> \
AZURE_CREATE_RESOURCE_GROUP=false \
AZURE_LOCATION=<resource-group-region> \
make azure-foundation-up
```

Use the same environment overrides for the rest of the Azure lifecycle. In this
mode Terraform reads the resource group as data, creates AKS and ACR inside it,
and leaves the resource group itself untouched during foundation destroy.

`make azure-up` runs:

```bash
make azure-infra-up
make azure-artifacts-deploy
```

`make azure-infra-up` builds and pushes the custom Superset image to ACR before
Terraform apply because the Superset Helm release waits for pods during install.
`make azure-artifacts-deploy` generates Floe manifests, builds and pushes the
project-code image, uploads manifests to the in-cluster SeaweedFS ops bucket,
imports Superset reports, deploys OpenMetadata metadata, and restarts Dagster.

The Azure POC keeps SeaweedFS, PostgreSQL, Polaris, and Kubernetes Secrets
in-cluster. Azure Blob/ADLS, Azure PostgreSQL Flexible Server, Key Vault,
managed identity-backed runtime auth, ingress, DNS, and TLS are future phases,
not active POC implementations.
