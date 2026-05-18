# Terraform

Terraform will own environment assembly and reusable platform modules.

Planned structure:

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

No Terraform modules are implemented in Iteration 0.
