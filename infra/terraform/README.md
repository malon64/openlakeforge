# Terraform

Terraform will own environment assembly and reusable platform modules.

Implemented structure:

```text
infra/terraform/
├── foundations/
│   └── local-kind/
├── environments/
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

The local foundation root creates the kind cluster. The local environment root
then deploys OpenLakeForge into that cluster through the Kubernetes and Helm
providers. Static, non-secret Helm chart values live in `../helm/values/local`;
Terraform modules overlay the dynamic contract values and Secret references.
Local is the only implemented provider profile today, but its module outputs
are shaped as provider contracts so a future cloud profile can swap
implementations.

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
- Dagster webserver, daemon, aggregate product code server, and Kubernetes run launcher
- Superset webserver, worker, reports volume, and local report deploy path
- OpenMetadata, Polaris service metadata, and catalog ingestion plumbing

`make local-artifacts-deploy` owns the local/CD artifacts:

- project-code image build/load
- product Floe manifest generation and upload to the local code bucket
- product Superset report import
- OpenMetadata domain, data-product, Bronze, Silver, and Gold metadata from domain YAML files
- Dagster rollout after dynamic artifacts are available

Terraform state is local and contains generated development credentials. Treat
state files as sensitive; they are gitignored.

No AWS environment, AWS provider blocks, remote state backend, Keycloak, Vault,
or cloud secret manager integration is implemented yet.
