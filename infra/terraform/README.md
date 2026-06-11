# Terraform

Terraform will own environment assembly and reusable platform modules.

Implemented local structure:

```text
infra/terraform/
├── environments/
│   └── local/
└── modules/
    ├── platform/
    ├── storage/seaweedfs/
    ├── catalog/polaris/
    ├── query/trino/
    ├── analytics/superset/
    ├── orchestration/dagster/
    ├── database/postgres/
    ├── security/
    ├── governance/
    └── observability/
```

The local environment deploys into the active Kubernetes context. It does not
create the kind cluster; use `make local-cluster` for that. Static, non-secret
Helm chart values live in `../helm/values/local`; Terraform modules overlay the
dynamic contract values and Secret references.

## Local workflow

```bash
make local-up
make local-down
```

`make local-up` runs two phases:

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
- Dagster webserver, daemon, sales code server, and Kubernetes run launcher
- Superset webserver, worker, reports volume, and local report deploy path
- OpenMetadata, OpenLineage proxy, Polaris service metadata, and catalog ingestion plumbing

`make local-artifacts-deploy` owns the local/CD artifacts:

- project-code image build/load
- Sales Floe manifest generation and upload to the local code bucket
- Sales Superset report import
- OpenMetadata domain and data-product metadata from domain YAML files
- Dagster rollout after dynamic artifacts are available

Terraform state is local and contains generated development credentials. Treat
state files as sensitive; they are gitignored.
