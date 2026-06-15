# Infrastructure

Infrastructure definitions live under `terraform/`, `helm/`, and `kind/`.

Terraform owns the local and Azure POC lifecycle phases:

- `terraform/foundations/local-kind/` creates and destroys the local kind
  cluster from the configuration in `kind/local/`.
- `terraform/environments/local/` assembles SeaweedFS, Polaris, Trino,
  OpenMetadata, Superset, and Dagster on that cluster.
- `terraform/foundations/azure-aks/` creates and destroys the Azure POC
  resource group, AKS cluster, ACR registry, and AKS-to-ACR pull permission.
- `terraform/environments/azure-poc/` assembles the same in-cluster services on
  AKS while emitting Azure-specific provider contracts.
