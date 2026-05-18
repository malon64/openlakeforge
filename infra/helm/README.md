# Helm

The local stack consumes upstream Helm charts through Terraform `helm_release`
resources. Static, non-secret local chart values live in YAML files here.
Terraform modules layer dynamic values on top for generated credentials, service
contract endpoints, Secret references, and bootstrap run markers.

## Structure

```text
infra/helm/
└── values/
    └── local/
        ├── seaweedfs.yaml
        ├── polaris.yaml
        └── trino.yaml
```

## Local charts

- **SeaweedFS** (`seaweedfs/seaweedfs`)
  Chart source: https://seaweedfs.github.io/seaweedfs/helm
- **Apache Polaris** (`polaris/polaris`)
  Chart source: https://downloads.apache.org/polaris/helm-chart
- **Trino** (`trino/trino`)
  Chart source: https://trinodb.github.io/charts

## Workflow

```bash
make local-cluster
make local-up
make local-forward
make local-down
make local-destroy-cluster
```
