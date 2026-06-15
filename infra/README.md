# Infrastructure

Infrastructure definitions live under `terraform/`, `helm/`, and `kind/`.

Terraform owns both local lifecycle phases:

- `terraform/foundations/local-kind/` creates and destroys the local kind
  cluster from the configuration in `kind/local/`.
- `terraform/environments/local/` assembles SeaweedFS, Polaris, Trino,
  OpenMetadata, Superset, and Dagster on that cluster.
