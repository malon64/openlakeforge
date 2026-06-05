# Infrastructure

Infrastructure definitions live under `terraform/` and `helm/`.

Terraform owns the local lakehouse assembly for SeaweedFS, Polaris, Trino,
OpenMetadata, Superset, and Dagster. Local kind cluster configuration lives
under `kind/local/`; lifecycle scripts live under `../scripts/local/`.
