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

`make local-up` runs `terraform init` and `terraform apply` in
`infra/terraform/environments/local`. Terraform owns:

- Kubernetes namespace creation
- SeaweedFS, Polaris, and Trino Helm releases
- Dagster Helm release
- dynamic Helm values passed to those releases
- local generated credentials
- Kubernetes Secrets used as service contracts
- SeaweedFS bucket creation jobs
- Polaris catalog and Trino principal bootstrap jobs
- Polaris Floe principal bootstrap credentials for manifest-driven Floe jobs
- chart-managed local Dagster PostgreSQL
- Dagster webserver, daemon, sales code server, and Kubernetes run launcher

Terraform state is local and contains generated development credentials. Treat
state files as sensitive; they are gitignored.
